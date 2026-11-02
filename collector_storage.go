package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
)

var pressedModifiers = map[uint16]bool{}
var sensitivePatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)\S+`),
	regexp.MustCompile(`(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|otp|verification[_ ]code)\s*[=:]\s*["']?)[^\s&"']+`),
	regexp.MustCompile(`(?i)([?&](?:token|key|code|auth|password|secret)=)[^&#\s]+`),
}
var tokenPattern = regexp.MustCompile(`\b(?:gsk_|sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}\b`)

func redactContent(text string) string {
	for _, pattern := range sensitivePatterns {
		text = pattern.ReplaceAllString(text, "${1}[REDACTED]")
	}
	text = regexp.MustCompile(`(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----`).ReplaceAllString(text, "[REDACTED PRIVATE KEY]")
	return tokenPattern.ReplaceAllString(text, "[REDACTED]")
}
func ensureEventSchema(db *sql.DB) error {
	db.SetMaxOpenConns(1)
	rows, err := db.Query("PRAGMA table_info(events)")
	if err != nil {
		return err
	}
	existing := map[string]bool{}
	for rows.Next() {
		var id, notNull, pk int
		var name, typ string
		var def interface{}
		if err := rows.Scan(&id, &name, &typ, &notNull, &def, &pk); err != nil {
			return err
		}
		existing[name] = true
	}
	rows.Close()
	columns := map[string]string{"source_event_id": "TEXT", "account_id": "TEXT DEFAULT ''", "conversation_id": "TEXT DEFAULT ''", "actor_id": "TEXT DEFAULT ''", "action": "TEXT DEFAULT 'observed'", "direction": "TEXT DEFAULT 'unknown'", "ingested_at": "TEXT", "metadata": "TEXT DEFAULT '{}'", "embedding_version": "TEXT", "embedding_status": "TEXT DEFAULT 'pending'", "embedding_attempts": "INTEGER DEFAULT 0", "next_embedding_at": "TEXT", "schema_version": "INTEGER DEFAULT 1"}
	for name, typ := range columns {
		if !existing[name] {
			if _, err := db.Exec("ALTER TABLE events ADD COLUMN " + name + " " + typ); err != nil {
				return err
			}
		}
	}
	if _, err = db.Exec("CREATE TABLE IF NOT EXISTS event_tombstones(key TEXT PRIMARY KEY)"); err != nil {
		return err
	}
	_, err = db.Exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source ON events(event_type,account_id,source_event_id) WHERE source_event_id IS NOT NULL")
	return err
}
func captureAllowed(db *sql.DB, source string) bool {
	var raw string
	if db.QueryRow("SELECT value FROM settings WHERE key='paused'").Scan(&raw) == nil && raw == "true" {
		return false
	}
	if db.QueryRow("SELECT value FROM settings WHERE key='excluded_sources'").Scan(&raw) == nil {
		var values []string
		json.Unmarshal([]byte(raw), &values)
		for _, v := range values {
			if v == source {
				return false
			}
		}
	}
	return true
}
func persistEvent(db *sql.DB, source, app, content string, occurred time.Time, sourceID string) bool {
	if !captureAllowed(db, source) {
		return true
	}
	content = redactContent(content)
	app = redactContent(app)
	if len(content) > 100000 {
		content = content[:100000]
	}
	if sourceID == "" {
		sourceID = randomID()
	}
	hash := sha256.Sum256([]byte(sourceID))
	sourceID = hex.EncodeToString(hash[:])
	tombstone := sha256.Sum256([]byte(source + "\x00\x00" + sourceID))
	var deleted int
	if db.QueryRow("SELECT 1 FROM event_tombstones WHERE key=?", hex.EncodeToString(tombstone[:])).Scan(&deleted) == nil {
		return true
	}
	action := "observed"
	if source == "error" {
		action = "error"
	}
	if source == "command" {
		action = "command"
	}
	if source == "history" || source == "tab" {
		action = "view"
	}
	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec("INSERT OR IGNORE INTO events(timestamp,event_type,app,content,source_event_id,action,ingested_at,schema_version) VALUES(?,?,?,?,?,?,?,2)", occurred.UTC().Format("2006-01-02 15:04:05"), source, app, content, sourceID, action, now)
	if err == nil {
		return true
	}
	dir := filepath.Join(qareenDataDir(), "outbox")
	if os.MkdirAll(dir, 0700) != nil {
		return false
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) >= 1000 {
		logError("Collector queue full; cursor retained")
		return false
	}
	// Durable retry file, written atomically, acknowledged before advancing source cursors.
	event := map[string]interface{}{"event_type": source, "app": app, "content": content, "source_event_id": sourceID, "action": action, "occurred_at": occurred.UTC().Format(time.RFC3339)}
	raw, _ := json.Marshal(event)
	path := filepath.Join(dir, sourceID+".json")
	if err := atomicPrivateFile(path, raw); err != nil {
		logError("Unable to persist collector event; cursor retained")
		return false
	}
	logError("Database temporarily unavailable; event queued locally")
	return true
}
func atomicPrivateFile(path string, data []byte) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), ".qareen-tmp-")
	if err != nil {
		return err
	}
	name := tmp.Name()
	defer os.Remove(name)
	if _, err = tmp.Write(data); err == nil {
		err = tmp.Sync()
	}
	closeErr := tmp.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(name, path)
}
func drainOutbox() {
	for {
		entries, _ := os.ReadDir(filepath.Join(qareenDataDir(), "outbox"))
		for _, entry := range entries {
			if !strings.HasSuffix(entry.Name(), ".json") {
				continue
			}
			path := filepath.Join(qareenDataDir(), "outbox", entry.Name())
			raw, err := os.ReadFile(path)
			if err != nil {
				continue
			}
			var event map[string]interface{}
			if json.Unmarshal(raw, &event) != nil {
				continue
			}
			if callAPI("POST", "/ingest", event, nil) == nil {
				os.Remove(path)
			}
		}
		time.Sleep(5 * time.Second)
	}
}

type historyCursor struct {
	Offset int64  `json:"offset"`
	Inode  uint64 `json:"inode"`
	Epoch  int64  `json:"epoch"`
}

func scanShellHistory(db *sql.DB, path string, fallback int64, zsh bool) int64 {
	file, err := os.Open(path)
	if err != nil {
		return fallback
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return fallback
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return fallback
	}
	hash := sha256.Sum256([]byte(path))
	statePath := filepath.Join(qareenDataDir(), fmt.Sprintf("history-%x.json", hash[:8]))
	cursor := historyCursor{Offset: fallback, Inode: stat.Ino}
	if raw, err := os.ReadFile(statePath); err == nil {
		json.Unmarshal(raw, &cursor)
	}
	if cursor.Inode != stat.Ino || info.Size() < cursor.Offset {
		cursor.Offset = 0
		cursor.Inode = stat.Ino
	}
	if _, err = file.Seek(cursor.Offset, 0); err != nil {
		return fallback
	}
	reader := bufio.NewReaderSize(file, 64*1024)
	for {
		line, err := readHistoryLine(reader)
		if err != nil {
			break
		} // Retain incomplete trailing line.
		consumed := int64(len(line))
		text := strings.TrimSpace(line)
		for strings.HasSuffix(text, "\\") {
			part, err := readHistoryLine(reader)
			if err != nil {
				return cursor.Offset
			}
			consumed += int64(len(part))
			text = strings.TrimSuffix(text, "\\") + "\n" + strings.TrimSpace(part)
			if len(text) > 100000 {
				return cursor.Offset
			}
		}
		if !zsh && strings.HasPrefix(text, "#") {
			if epoch, err := strconv.ParseInt(strings.TrimPrefix(text, "#"), 10, 64); err == nil {
				cursor.Epoch = epoch
				text = ""
			}
		}
		occurred := time.Now().UTC()
		if zsh && strings.HasPrefix(text, ": ") {
			parts := strings.SplitN(text, ";", 2)
			if len(parts) == 2 {
				header := strings.Split(strings.TrimPrefix(parts[0], ": "), ":")
				epoch, _ := strconv.ParseInt(header[0], 10, 64)
				if epoch > 0 {
					occurred = time.Unix(epoch, 0).UTC()
				}
				text = parts[1]
			}
		}
		if !zsh && cursor.Epoch > 0 {
			occurred = time.Unix(cursor.Epoch, 0).UTC()
		}
		shell := "bash"
		if zsh {
			shell = "zsh"
		}
		if text != "" && !persistEvent(db, "command", shell, "Observed shell history: "+text, occurred, fmt.Sprintf("%s:%d:%d", path, stat.Ino, cursor.Offset)) {
			break
		}
		cursor.Offset += consumed
		raw, _ := json.Marshal(cursor)
		if atomicPrivateFile(statePath, raw) != nil {
			break
		}
	}
	return cursor.Offset
}
func collectJournal(db *sql.DB) {
	state := filepath.Join(qareenDataDir(), "journal.cursor")
	for {
		args := []string{"--no-pager", "--output=json", "--show-cursor", "-p", "3"}
		cursor, _ := os.ReadFile(state)
		if len(cursor) > 0 {
			args = append(args, "--after-cursor", strings.TrimSpace(string(cursor)))
		} else {
			args = append(args, "--since", "-5 minutes")
		}
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		cmd := exec.CommandContext(ctx, "journalctl", args...)
		stdout, err := cmd.StdoutPipe()
		if err == nil {
			err = cmd.Start()
		}
		last := ""
		if err == nil {
			scanner := bufio.NewScanner(io.LimitReader(stdout, 16*1024*1024))
			scanner.Buffer(make([]byte, 64*1024), 1024*1024)
			for scanner.Scan() {
				line := scanner.Text()
				if strings.HasPrefix(line, "-- cursor:") {
					continue
				}
				var event map[string]interface{}
				if json.Unmarshal([]byte(line), &event) != nil {
					continue
				}
				message, _ := event["MESSAGE"].(string)
				id, _ := event["__CURSOR"].(string)
				micros, _ := event["__REALTIME_TIMESTAMP"].(string)
				unit, _ := event["_SYSTEMD_UNIT"].(string)
				t, _ := strconv.ParseInt(micros, 10, 64)
				if message != "" && id != "" {
					if !persistEvent(db, "error", unit, message, time.UnixMicro(t).UTC(), id) {
						break
					}
					last = id
				}
			}
			if scanner.Err() != nil {
				logError("Journal read interrupted; replaying from last committed cursor")
			}
			cancel()
			cmd.Wait()
			if last != "" {
				atomicPrivateFile(state, []byte(last))
			}
		} else {
			logError("Journal collector unavailable")
		}
		cancel()
		time.Sleep(15 * time.Second)
	}
}

// Bound allocation for malformed or exceptionally long history records.
func readHistoryLine(reader *bufio.Reader) (string, error) {
	var line strings.Builder
	for {
		fragment, err := reader.ReadSlice('\n')
		if line.Len()+len(fragment) > 100000 {
			return "", fmt.Errorf("history record exceeds 100000 bytes")
		}
		line.Write(fragment)
		if err == bufio.ErrBufferFull {
			continue
		}
		return line.String(), err
	}
}
