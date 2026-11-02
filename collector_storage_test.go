package main

import (
	"database/sql"
	"encoding/binary"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func fixtureDB(t *testing.T) *sql.DB {
	t.Helper()
	t.Setenv("QAREEN_DATA_DIR", t.TempDir())
	db, err := sql.Open("sqlite", filepath.Join(t.TempDir(), "events.db"))
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp TEXT,event_type TEXT,app TEXT,content TEXT,embedding BLOB)")
	if err != nil {
		t.Fatal(err)
	}
	if err = ensureEventSchema(db); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}
func TestPersistRedactsAndDeduplicates(t *testing.T) {
	db := fixtureDB(t)
	for i := 0; i < 2; i++ {
		if !persistEvent(db, "command", "shell", "password=secret", time.Now(), "same-source") {
			t.Fatal("persist failed")
		}
	}
	var n int
	var content string
	db.QueryRow("SELECT count(*),content FROM events").Scan(&n, &content)
	if n != 1 || strings.Contains(content, "secret") {
		t.Fatalf("count=%d content=%s", n, content)
	}
}
func TestCapturePause(t *testing.T) {
	db := fixtureDB(t)
	db.Exec("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
	db.Exec("INSERT INTO settings VALUES('paused','true')")
	persistEvent(db, "command", "shell", "private", time.Now(), "id")
	var n int
	db.QueryRow("SELECT count(*) FROM events").Scan(&n)
	if n != 0 {
		t.Fatal("captured while paused")
	}
}
func TestHistoryPartialLineAndRotation(t *testing.T) {
	db := fixtureDB(t)
	path := filepath.Join(t.TempDir(), "history")
	os.WriteFile(path, []byte("#1700000000\necho first\necho part"), 0600)
	offset := scanShellHistory(db, path, 0, false)
	var count int
	db.QueryRow("SELECT count(*) FROM events").Scan(&count)
	if count != 1 {
		t.Fatal(count)
	}
	f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
	f.WriteString("ial\n")
	f.Close()
	offset = scanShellHistory(db, path, offset, false)
	db.QueryRow("SELECT count(*) FROM events").Scan(&count)
	if count != 2 {
		t.Fatal(count)
	}
	os.Rename(path, path+".old")
	os.WriteFile(path, []byte("echo rotated\n"), 0600)
	scanShellHistory(db, path, offset, false)
	db.QueryRow("SELECT count(*) FROM events").Scan(&count)
	if count != 3 {
		t.Fatal(count)
	}
	var timestamp string
	db.QueryRow("SELECT timestamp FROM events ORDER BY id LIMIT 1").Scan(&timestamp)
	if timestamp != "2023-11-14 22:13:20" {
		t.Fatal(timestamp)
	}
}
func TestZshExtendedAndPlain(t *testing.T) {
	db := fixtureDB(t)
	path := filepath.Join(t.TempDir(), "history")
	os.WriteFile(path, []byte(": 1700000000:0;echo one\necho two\n"), 0600)
	scanShellHistory(db, path, 0, true)
	var count int
	db.QueryRow("SELECT count(*) FROM events").Scan(&count)
	if count != 2 {
		t.Fatal(count)
	}
}
func TestBinaryEmbeddingJSONPrefix(t *testing.T) {
	raw := make([]byte, EmbedDim*4)
	binary.LittleEndian.PutUint32(raw, math.Float32bits(.1))
	raw[0] = '['
	decoded, ok := decodeEmbedding(raw)
	if !ok || len(decoded) != EmbedDim {
		t.Fatal("binary mistaken for JSON")
	}
}
func TestModifierPairsRemainPressed(t *testing.T) {
	pressedModifiers = map[uint16]bool{}
	activeModifiers = 0
	handleInputEvent(nil, inputEvent{Type: 1, Code: 42, Value: 1})
	handleInputEvent(nil, inputEvent{Type: 1, Code: 54, Value: 1})
	handleInputEvent(nil, inputEvent{Type: 1, Code: 42, Value: 0})
	if activeModifiers&1 == 0 {
		t.Fatal("right shift lost when left shift released")
	}
	handleInputEvent(nil, inputEvent{Type: 1, Code: 54, Value: 0})
	if activeModifiers&1 != 0 {
		t.Fatal("shift stuck")
	}
}
func TestSQLiteFTS5AvailableToCollector(t *testing.T) {
	db := fixtureDB(t)
	if _, err := db.Exec("CREATE VIRTUAL TABLE probe USING fts5(content)"); err != nil {
		t.Fatal(err)
	}
}
