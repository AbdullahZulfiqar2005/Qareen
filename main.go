package main

import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"embed"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io/fs"
	"math"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"

	"time"
	"unsafe"

	"golang.org/x/sys/unix"

	_ "github.com/glebarez/go-sqlite"
)

// Global Configuration
const (
	ServerPort    = "2846"
	ServerURL     = "http://127.0.0.1:" + ServerPort
	DashboardPort = "2847"
	GroqModel     = "llama-3.3-70b-versatile"
	EmbedDim      = 384 // output dimension of the all-MiniLM-L6-v2 sentence embedding model
)

//go:embed dashboard/*
var dashboardFS embed.FS

// ANSI Colors
const (
	Blue    = "\033[94m"
	Green   = "\033[92m"
	Yellow  = "\033[93m"
	Red     = "\033[91m"
	Magenta = "\033[95m"
	Cyan    = "\033[96m"
	Gray    = "\033[90m"
	Bold    = "\033[1m"
	Reset   = "\033[0m"
)

type Event struct {
	ID        int
	Timestamp string
	Type      string
	App       string
	Content   string
	Embedding []float64
}

type TabInfo struct {
	Title string `json:"title"`
	URL   string `json:"url"`
}

type SimilarityMatch struct {
	Event Event
	Score float64
}

// loadDotEnv looks for a .env file (in $QAREEN_ENV_FILE, ~/.config/qareen/.env,
// ~/.qareen.env, or the current working directory, in that order) and, for
// every KEY=VALUE line it finds, sets that environment variable if it isn't
// already set. This means GROQ_API_KEY (and anything else future components
// need) can just live in a .env file instead of requiring a manual
// `export GROQ_API_KEY=...` in the shell profile. Lines may optionally start
// with "export " (as .env.example does) and values may be quoted; comments
// (#) and blank lines are ignored. Real env vars set in the shell always take
// priority over the .env file.
func loadDotEnv() {
	var candidates []string
	if v := os.Getenv("QAREEN_ENV_FILE"); v != "" {
		candidates = append(candidates, v)
	}
	if home, err := os.UserHomeDir(); err == nil {
		candidates = append(candidates, filepath.Join(home, ".config", "qareen", ".env"))
		candidates = append(candidates, filepath.Join(home, ".qareen.env"))
	}
	candidates = append(candidates, ".env")

	for _, path := range candidates {
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(data), "\n") {
			line = strings.TrimSpace(line)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			line = strings.TrimPrefix(line, "export ")
			parts := strings.SplitN(line, "=", 2)
			if len(parts) != 2 {
				continue
			}
			key := strings.TrimSpace(parts[0])
			val := strings.TrimSpace(parts[1])
			val = strings.Trim(val, `"'`)
			if key == "" {
				continue
			}
			if _, exists := os.LookupEnv(key); !exists && val != "" {
				os.Setenv(key, val)
			}
		}
		// Only the first .env file found is used, mirroring how shell
		// profiles work (first match wins rather than merging silently).
		break
	}
}

func main() {
	syscall.Umask(0077)
	loadDotEnv()

	if len(os.Args) < 2 {
		printUsage()
		return
	}

	command := os.Args[1]
	switch command {
	case "token":
		role := "admin"
		if len(os.Args) > 2 {
			role = os.Args[2]
		}
		if role != "admin" && role != "ingest" {
			fmt.Fprintln(os.Stderr, "role must be admin or ingest")
			os.Exit(1)
		}
		content, err := os.ReadFile(filepath.Join(qareenDataDir(), role+".token"))
		if err != nil {
			fmt.Fprintln(os.Stderr, "Start the service first")
			os.Exit(1)
		}
		fmt.Println(strings.TrimSpace(string(content)))
	case "doctor":
		runDoctor()
	case "pause", "resume":
		if err := callAPI("POST", "/api/settings", map[string]interface{}{"paused": command == "pause"}, nil); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	case "export":
		var result interface{}
		if err := callAPI("GET", "/api/export", nil, &result); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		json.NewEncoder(os.Stdout).Encode(result)
	case "start":
		startDaemon()
	case "stop":
		stopDaemon()
	case "status":
		statusDaemon()
	case "run-daemon":
		os.Exit(runDaemonLoop())
	case "query":
		if len(os.Args) < 3 {
			fmt.Println("Error: Query text required. Usage: qareen query \"<issue>\"")
			return
		}
		queryDaemon(strings.Join(os.Args[2:], " "))
	case "list":
		limit := 20
		if len(os.Args) >= 3 {
			if l, err := strconv.Atoi(os.Args[2]); err == nil {
				limit = l
			}
		}
		listEvents(limit)
	case "clear":
		clearEvents()
	case "dashboard":
		serveDashboard()
	default:
		printUsage()
	}
}

func printUsage() {
	fmt.Printf("%s%s================ QAREEN USER DIGITAL TWIN ================%s\n", Blue, Bold, Reset)
	fmt.Println("Usage:")
	fmt.Println("  qareen start          Start the background logging daemon & embedding server")
	fmt.Println("  qareen stop           Stop all Qareen background processes")
	fmt.Println("  qareen status         Show status of the daemon and database statistics")
	fmt.Println("  qareen query <issue>  Search history and get AI-guided instructions on past fixes")
	fmt.Println("  qareen list [limit]   List the most recently logged events")
	fmt.Println("  qareen clear          Delete all logged events from the database")
	fmt.Println("  qareen dashboard      Open the web dashboard at http://localhost:2847")
}

// ---------------- DAEMON MANAGEMENT ----------------

func getPaths() (string, string, string, string) {
	home, _ := os.UserHomeDir()
	dbPath := os.Getenv("QAREEN_DB_PATH")
	if dbPath == "" {
		dbPath = filepath.Join(home, ".qareen.db")
	}
	runtimeDir := os.Getenv("XDG_RUNTIME_DIR")
	if runtimeDir == "" {
		runtimeDir = qareenDataDir()
	}
	return dbPath, filepath.Join(runtimeDir, "qareend.pid"), filepath.Join(runtimeDir, "qareen_py.pid"), filepath.Join(qareenDataDir(), "collector.log")
}

func startDaemon() {
	serviceControl("start")
}

func stopDaemon() {
	serviceControl("stop")
}

func statusDaemon() {
	serviceControl("status")
}

// ---------------- DAEMON RUN LOOP ----------------

func runDaemonLoop() int {
	dbPath, daemonPidPath, _, _ := getPaths()
	lock, err := os.OpenFile(daemonPidPath+".lock", os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		logError("Collector lock unavailable")
		return 1
	}
	defer lock.Close()
	if syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		logError("Collector already running")
		return 1
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)

	// Share the migration lock with the Python store during concurrent startup.
	schemaLock, err := os.OpenFile(dbPath+".migration.lock", os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		logError("Schema lock unavailable")
		return 1
	}
	defer schemaLock.Close()
	if err = syscall.Flock(int(schemaLock.Fd()), syscall.LOCK_EX); err != nil {
		return 1
	}
	// Setup database
	db, err := sql.Open("sqlite", dbPath+"?_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)")
	if err != nil {
		logError("Failed to open SQLite database: %v", err)
		return 1
	}
	defer db.Close()

	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS events (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
		event_type TEXT,
		app TEXT,
		content TEXT,
		embedding BLOB
	)`)
	if err != nil {
		logError("Failed to initialize database tables: %v", err)
		return 1
	}
	// Helpful for `qareen list` / status and any future timestamp-ordered
	// queries; without it every ORDER BY/LIMIT still does a full table scan.
	_, err = db.Exec(`CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)`)
	if err != nil {
		logError("Failed to create timestamp index: %v", err)
	}

	if err := ensureEventSchema(db); err != nil {
		logError("Schema initialization failed: %v", err)
		return 1
	}
	syscall.Flock(int(schemaLock.Fd()), syscall.LOCK_UN)
	os.Chmod(dbPath, 0600)
	logInfo("Daemon loop started (event bodies are not logged).")
	go drainOutbox()
	go collectJournal(db)

	// Start native Hyprland keybind tracker
	if os.Getenv("QAREEN_CAPTURE_KEYBINDS") == "1" {
		go startKeybindTracker(db)
	}

	// Start desktop notification watcher (dunst/mako/swaync/etc via DBus)
	go startNotificationWatcher(db)

	// Start file creation/deletion watcher (Downloads/Documents/Desktop)
	go startFileWatcher(db)

	// Signal handling for graceful shutdown
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	done := make(chan bool, 1)
	go func() {
		<-sigs
		logInfo("Terminating daemon loop gracefully...")
		done <- true
	}()

	// Initialize trackers state
	home, _ := os.UserHomeDir()

	// Active Window state
	var lastClass, lastTitle string

	// Shell History state
	var lastZshSize, lastBashSize int64
	zshPath := filepath.Join(home, ".zsh_history")
	bashPath := filepath.Join(home, ".bash_history")
	if fi, err := os.Stat(zshPath); err == nil {
		lastZshSize = fi.Size()
	}
	if fi, err := os.Stat(bashPath); err == nil {
		lastBashSize = fi.Size()
	}

	// Firefox History state
	lastPlacesTimestamp := time.Now().UnixMicro()
	firefoxCursorPath := filepath.Join(qareenDataDir(), "firefox.cursor")
	if raw, err := os.ReadFile(firefoxCursorPath); err == nil {
		if v, err := strconv.ParseInt(strings.TrimSpace(string(raw)), 10, 64); err == nil {
			lastPlacesTimestamp = v
		}
	}

	// Firefox Tabs state
	var lastTabs []string

	// System log tracking state

	// Loop tickers
	tickerFast := time.NewTicker(2 * time.Second)   // Active Window
	tickerMedium := time.NewTicker(5 * time.Second) // Shell Command / Firefox history
	tickerSlow := time.NewTicker(30 * time.Second)  // Open Tabs

	defer tickerFast.Stop()
	defer tickerMedium.Stop()
	defer tickerSlow.Stop()

	for {
		select {
		case <-done:
			logInfo("Daemon loop exited.")
			return 0
		case <-tickerFast.C:
			c, t, err := getActiveWindow()
			if err == nil && (c != lastClass || t != lastTitle) && (c != "" || t != "") {
				if logEvent(db, "window", c, "Observed active window title: "+t) {
					lastClass = c
					lastTitle = t
				}
			}

		case <-tickerMedium.C:
			lastZshSize = scanShellHistory(db, zshPath, lastZshSize, true)
			lastBashSize = scanShellHistory(db, bashPath, lastBashSize, false)
			if profile, err := findFirefoxProfile(); err == nil {
				latest, ok := scanFirefoxHistory(filepath.Join(profile, "places.sqlite"), lastPlacesTimestamp, db)
				if ok && latest > lastPlacesTimestamp {
					lastPlacesTimestamp = latest
					atomicPrivateFile(firefoxCursorPath, []byte(strconv.FormatInt(latest, 10)))
				}
			}

		case <-tickerSlow.C:
			// 4. Firefox Tabs tracking
			tabs, err := getFirefoxTabs()
			if err == nil {
				var currentTabStrings []string
				for _, tab := range tabs {
					currentTabStrings = append(currentTabStrings, fmt.Sprintf("%s (%s)", tab.Title, tab.URL))
				}
				// Compare with last tabs to see if new tabs are opened
				for _, tabStr := range currentTabStrings {
					isNew := true
					for _, oldTab := range lastTabs {
						if tabStr == oldTab {
							isNew = false
							break
						}
					}
					if isNew {
						logEvent(db, "tab", "firefox", fmt.Sprintf("Observed browser tab: %s", tabStr))
					}
				}
				lastTabs = currentTabStrings
			}

		}
	}
}

func getActiveWindow() (string, string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "hyprctl", "activewindow", "-j").Output()
	if err != nil {
		return "", "", err
	}
	var window struct {
		Class string `json:"class"`
		Title string `json:"title"`
	}
	err = json.Unmarshal(out, &window)
	return window.Class, window.Title, err
}

func getFirefoxTabs() ([]TabInfo, error) {
	resp, err := http.Get(ServerURL + "/firefox-tabs")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var res struct {
		Tabs []TabInfo `json:"tabs"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&res); err != nil {
		return nil, err
	}
	return res.Tabs, nil
}

func logEvent(db *sql.DB, eventType, app, content string) bool {
	return persistEvent(db, eventType, app, content, time.Now().UTC(), "")
}

// encodeEmbedding packs a []float64 embedding into a compact little-endian
// float32 byte blob (4 bytes/dim instead of ~20 bytes/dim as a JSON text
// string of float64s - roughly an 80% reduction in on-disk size per event,
// and much faster to (de)serialize since there's no JSON parsing involved).
func encodeEmbedding(embedding []float64) []byte {
	buf := make([]byte, 4*len(embedding))
	for i, v := range embedding {
		binary.LittleEndian.PutUint32(buf[i*4:], math.Float32bits(float32(v)))
	}
	return buf
}

// decodeEmbedding is the inverse of encodeEmbedding. It also transparently
// handles the old JSON-text format so existing databases created by earlier
// versions of Qareen keep working without a manual migration.
func decodeEmbedding(raw []byte) ([]float64, bool) {
	// Known binary format wins over legacy sniffing: a float can begin with '['.
	if len(raw) != EmbedDim*4 {
		var legacy []float64
		if json.Unmarshal(raw, &legacy) == nil && len(legacy) > 0 {
			return legacy, true
		}
		return nil, false
	}
	result := make([]float64, EmbedDim)
	for i := range result {
		result[i] = float64(math.Float32frombits(binary.LittleEndian.Uint32(raw[i*4:])))
	}
	return result, true
}

// ---------------- QUERY SEARCH & LLM ----------------

func queryDaemon(queryText string) {
	var answer struct {
		Answer  string `json:"answer"`
		Results []struct {
			ID        int    `json:"id"`
			Content   string `json:"content"`
			Timestamp string `json:"timestamp"`
		} `json:"results"`
		Coverage interface{} `json:"coverage"`
	}
	if err := callAPI("POST", "/api/ask", map[string]interface{}{"query": queryText}, &answer); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println(answer.Answer)
	for _, event := range answer.Results {
		fmt.Printf("[event:%d] %s %s\n", event.ID, event.Timestamp, event.Content)
	}
	fmt.Printf("Coverage: %v\n", answer.Coverage)
}

type WikiResult struct {
	Content string `json:"content"`
	Source  string `json:"source"`
}

// ---------------- UTILITIES & HELPERS ----------------

func cosineSimilarity(a, b []float64) float64 {
	if len(a) != len(b) {
		return 0
	}
	var dotProduct, normA, normB float64
	for i := 0; i < len(a); i++ {
		dotProduct += a[i] * b[i]
		normA += a[i] * a[i]
		normB += b[i] * b[i]
	}
	if normA == 0.0 || normB == 0.0 {
		return 0.0
	}
	return dotProduct / (math.Sqrt(normA) * math.Sqrt(normB))
}

// scanFirefoxHistory opens placesPath read-only (via SQLite URI mode=ro so it
// never blocks or is blocked by Firefox's own writer) and logs any visits
// newer than sinceMicros. Returns the latest visit timestamp seen and
// whether the scan succeeded. On failure retain the cursor; never copy a live
// SQLite database without its WAL.
func scanFirefoxHistory(placesPath string, sinceMicros int64, db *sql.DB) (int64, bool) {
	dsn := fmt.Sprintf("file:%s?mode=ro", placesPath)
	roDb, err := sql.Open("sqlite", dsn)
	if err != nil {
		return 0, false
	}
	defer roDb.Close()

	rows, err := roDb.Query(`
		SELECT h.visit_date, p.url, p.title
		FROM moz_historyvisits h
		JOIN moz_places p ON h.place_id = p.id
		WHERE h.visit_date >= ?
		ORDER BY h.visit_date ASC`, sinceMicros)
	if err != nil {
		return 0, false
	}
	defer rows.Close()

	var latestVisit int64
	for rows.Next() {
		var visitDate int64
		var url, title sql.NullString
		if err := rows.Scan(&visitDate, &url, &title); err == nil {

			tStr := title.String
			if tStr == "" {
				tStr = url.String
			}
			if !persistEvent(db, "history", "firefox", fmt.Sprintf("Visited webpage: %s (URL: %s)", tStr, url.String), time.UnixMicro(visitDate).UTC(), fmt.Sprintf("%d:%s", visitDate, url.String)) {
				return latestVisit, false
			}
			latestVisit = visitDate
		}
	}
	return latestVisit, rows.Err() == nil
}

func findFirefoxProfile() (string, error) {
	if path := os.Getenv("QAREEN_FIREFOX_PROFILE"); path != "" {
		return path, nil
	}
	home, _ := os.UserHomeDir()
	roots := []string{filepath.Join(home, ".mozilla/firefox"), filepath.Join(home, "snap/firefox/common/.mozilla/firefox"), filepath.Join(home, ".var/app/org.mozilla.firefox/.mozilla/firefox")}
	var candidates []string
	for _, root := range roots {
		data, err := os.ReadFile(filepath.Join(root, "profiles.ini"))
		if err != nil {
			continue
		}
		sections := parseINI(string(data))
		for name, values := range sections {
			if strings.HasPrefix(name, "Install") && values["Default"] != "" {
				return filepath.Join(root, values["Default"]), nil
			}
		}
		for name, values := range sections {
			if !strings.HasPrefix(name, "Profile") || values["Path"] == "" {
				continue
			}
			path := values["Path"]
			if values["IsRelative"] != "0" {
				path = filepath.Join(root, path)
			}
			if values["Default"] == "1" {
				return path, nil
			}
			candidates = append(candidates, path)
		}
	}
	if len(candidates) == 1 {
		return candidates[0], nil
	}
	return "", fmt.Errorf("set QAREEN_FIREFOX_PROFILE to select a profile")
}

func listEvents(limit int) {
	dbPath, _, _, _ := getPaths()
	db, err := sql.Open("sqlite", dbPath+"?_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)")
	if err != nil {
		fmt.Printf("Error: %v\n", err)
		return
	}
	defer db.Close()

	rows, err := db.Query("SELECT id, timestamp, event_type, app, content FROM events ORDER BY id DESC LIMIT ?", limit)
	if err != nil {
		fmt.Printf("Error querying events: %v\n", err)
		return
	}
	defer rows.Close()

	fmt.Printf("%s%sRecent Logged Events (Last %d):%s\n", Blue, Bold, limit, Reset)
	for rows.Next() {
		var id int
		var timestamp, eventType, app, content string
		if err := rows.Scan(&id, &timestamp, &eventType, &app, &content); err == nil {
			fmt.Printf("  [%d] [%s] [%s] [%s] %s\n", id, timestamp, strings.ToUpper(eventType), app, content)
		}
	}
}

func clearEvents() {
	var result interface{}
	if err := callAPI("POST", "/api/delete", map[string]interface{}{"all": true}, &result); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println("Cleared event memory, associated proposals and legacy derived copies.")
}

func logInfo(format string, args ...interface{}) {
	msg := fmt.Sprintf(format, args...)
	fmt.Printf("[INFO] [%s] %s\n", time.Now().Format("2006-01-02 15:04:05"), msg)
}

func logError(format string, args ...interface{}) {
	msg := fmt.Sprintf(format, args...)
	fmt.Fprintf(os.Stderr, "[ERROR] [%s] %s\n", time.Now().Format("2006-01-02 15:04:05"), msg)
}

// ---------------- HYPRLAND KEYBIND TRACKER ----------------

type HyprBind struct {
	Locked     bool   `json:"locked"`
	Mouse      bool   `json:"mouse"`
	Release    bool   `json:"release"`
	Repeat     bool   `json:"repeat"`
	Modmask    int    `json:"modmask"`
	Key        string `json:"key"`
	Keycode    int    `json:"keycode"`
	Dispatcher string `json:"dispatcher"`
	Arg        string `json:"arg"`
}

type inputEvent struct {
	TimeSec  int64
	TimeUsec int64
	Type     uint16
	Code     uint16
	Value    int32
}

var (
	activeModifiers int
	cachedBinds     []HyprBind
	bindsMutex      sync.RWMutex
)

// startNotificationWatcher perceives desktop notifications (dunst, mako,
// swaync, or any other DBus-compliant notification daemon) by eavesdropping
// on calls to org.freedesktop.Notifications.Notify on the session bus via
// `dbus-monitor`. This is the same mechanism dedicated notification-log
// tools use, and it requires no new library dependency - just the `dbus`
// package that's already present on virtually every Linux desktop.
//
// If dbus-monitor isn't installed or the session bus can't be reached (e.g.
// running headless, or eavesdrop is blocked by bus policy), this logs a
// clear warning once and returns without affecting any other tracker.
func startNotificationWatcher(db *sql.DB) {
	logInfo("Initializing desktop notification watcher (dbus-monitor)...")

	if _, err := exec.LookPath("dbus-monitor"); err != nil {
		logError("dbus-monitor not found on PATH; desktop notifications will not be tracked. Install the 'dbus' package to enable this.")
		return
	}

	for {
		if err := runNotificationMonitor(db); err != nil {
			logError("Notification watcher error (will retry in 10s): %v", err)
		}
		time.Sleep(10 * time.Second)
	}
}

// runNotificationMonitor runs a single dbus-monitor session and parses its
// output until the process exits or errors, at which point the caller
// retries. Returning (rather than looping forever inside) lets
// startNotificationWatcher log+backoff consistently on repeated failures.
func runNotificationMonitor(db *sql.DB) error {
	cmd := exec.Command("dbus-monitor", "--session",
		"interface='org.freedesktop.Notifications',member='Notify',eavesdrop=true")

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("failed to attach to dbus-monitor stdout: %w", err)
	}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("failed to start dbus-monitor: %w", err)
	}

	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 64*1024), 1024*1024)

	// dbus-monitor prints each Notify() call as a "method call" header line
	// followed by its arguments, one per line, each indented by exactly 3
	// spaces for top-level args (nested array/dict contents are indented
	// further and are intentionally ignored here). The Notify() signature is
	// (app_name, replaces_id, app_icon, summary, body, actions, hints,
	// expire_timeout) - so top-level arg #1 is app_name, #4 is summary, #5 is
	// body.
	stringArgRe := regexp.MustCompile(`^string "(.*)"$`)
	inCall := false
	argIdx := 0
	var appName, summary, body string

	flush := func() {
		if appName != "" || summary != "" || body != "" {
			content := fmt.Sprintf("Notification from %s: %s - %s", appName, summary, body)
			logEvent(db, "notification", appName, content)
		}
		appName, summary, body = "", "", ""
		argIdx = 0
		inCall = false
	}

	for scanner.Scan() {
		line := scanner.Text()
		trimmed := strings.TrimSpace(line)

		if strings.HasPrefix(trimmed, "method call") {
			if inCall {
				flush() // a new call started before we saw a blank separator - flush the previous one
			}
			if strings.Contains(line, "member=Notify") {
				inCall = true
				argIdx = 0
			}
			continue
		}
		if !inCall {
			continue
		}

		leading := len(line) - len(strings.TrimLeft(line, " "))
		if leading == 0 {
			// A non-indented line with no "method call" prefix marks the end
			// of this entry's arguments (e.g. "signal time=..." for an
			// unrelated event interleaved on the bus).
			flush()
			continue
		}
		if leading == 3 {
			argIdx++
			if m := stringArgRe.FindStringSubmatch(trimmed); m != nil {
				switch argIdx {
				case 1:
					appName = m[1]
				case 4:
					summary = m[1]
				case 5:
					body = m[1]
				}
			}
		}
		// Deeper indentation = nested array/dict contents (actions, hints) -
		// intentionally skipped.
	}
	flush()

	if err := cmd.Wait(); err != nil {
		return fmt.Errorf("dbus-monitor exited: %w", err)
	}
	return scanner.Err()
}

// startFileWatcher perceives file creation/deletion (including moves, which
// show up as CREATE-at-destination / DELETE-at-source) in the user's common
// working directories via inotify. Uses golang.org/x/sys/unix directly
// (already an indirect dependency via github.com/glebarez/go-sqlite) rather
// than pulling in a new third-party fsnotify-style library.
//
// Directories that don't exist on this system (e.g. no ~/Desktop) are
// skipped silently; if none of the configured directories exist, or inotify
// itself is unavailable, this logs a warning once and returns without
// affecting any other tracker.
func startFileWatcher(db *sql.DB) {
	home, err := os.UserHomeDir()
	if err != nil {
		logError("Could not determine home directory; file tracking disabled: %v", err)
		return
	}

	watchDirs := []string{
		filepath.Join(home, "Downloads"),
		filepath.Join(home, "Documents"),
		filepath.Join(home, "Desktop"),
	}

	fd, err := unix.InotifyInit1(unix.IN_NONBLOCK)
	if err != nil {
		logError("Failed to initialize inotify; file tracking disabled: %v", err)
		return
	}
	defer unix.Close(fd)

	watchToDir := make(map[int32]string)
	const flags = unix.IN_CREATE | unix.IN_MOVED_TO | unix.IN_DELETE | unix.IN_MOVED_FROM | unix.IN_DELETE_SELF | unix.IN_MOVE_SELF

	for _, dir := range watchDirs {
		if fi, err := os.Stat(dir); err != nil || !fi.IsDir() {
			continue
		}
		wd, err := unix.InotifyAddWatch(fd, dir, flags)
		if err != nil {
			logError("Failed to watch directory %s: %v", dir, err)
			continue
		}
		watchToDir[int32(wd)] = dir
		logInfo("Watching directory for file changes: %s", dir)
	}

	// Debounce: editors/downloaders often fire several inotify events for
	// the same path within milliseconds of each other (e.g. a temp file then
	// a rename-into-place). Skip repeats of the same path within this window
	// so one save doesn't become three near-identical DB rows.
	lastLogged := make(map[string]time.Time)
	buf := make([]byte, 4096)
	lastRearm := time.Time{}
	for {
		if time.Since(lastRearm) > 5*time.Second {
			lastRearm = time.Now()
			for _, dir := range watchDirs {
				found := false
				for _, active := range watchToDir {
					if active == dir {
						found = true
					}
				}
				if !found {
					if wd, err := unix.InotifyAddWatch(fd, dir, flags); err == nil {
						watchToDir[int32(wd)] = dir
					}
				}
			}
		}
		n, err := unix.Read(fd, buf)
		if err != nil {
			if err == unix.EAGAIN {
				time.Sleep(500 * time.Millisecond)
				continue
			}
			logError("inotify read error (will retry): %v", err)
			time.Sleep(2 * time.Second)
			continue
		}

		offset := 0
		for offset+int(unix.SizeofInotifyEvent) <= n {
			raw := (*unix.InotifyEvent)(unsafe.Pointer(&buf[offset]))
			nameLen := int(raw.Len)
			var name string
			if nameLen > 0 && offset+int(unix.SizeofInotifyEvent)+nameLen <= n {
				nameBytes := buf[offset+int(unix.SizeofInotifyEvent) : offset+int(unix.SizeofInotifyEvent)+nameLen]
				name = strings.TrimRight(string(nameBytes), "\x00")
			}
			if raw.Mask&unix.IN_Q_OVERFLOW != 0 {
				logError("File event queue overflow; capture is incomplete")
			}
			if raw.Mask&(unix.IN_IGNORED|unix.IN_DELETE_SELF|unix.IN_MOVE_SELF) != 0 {
				delete(watchToDir, raw.Wd)
				unix.InotifyRmWatch(fd, uint32(raw.Wd))
			}
			dir := watchToDir[raw.Wd]
			offset += int(unix.SizeofInotifyEvent) + nameLen

			if name == "" || dir == "" {
				continue
			}

			fullPath := filepath.Join(dir, name)
			debounceKey := fmt.Sprintf("%s:%d", fullPath, raw.Mask)
			if len(lastLogged) > 1024 {
				for key, t := range lastLogged {
					if time.Since(t) > 3*time.Second {
						delete(lastLogged, key)
					}
				}
			}
			if t, ok := lastLogged[debounceKey]; ok && time.Since(t) < 200*time.Millisecond {
				continue
			}
			if len(lastLogged) >= 2048 {
				clear(lastLogged)
			}
			lastLogged[debounceKey] = time.Now()

			var action string
			switch {
			case raw.Mask&unix.IN_CREATE != 0 || raw.Mask&unix.IN_MOVED_TO != 0:
				action = "created"
			case raw.Mask&unix.IN_DELETE != 0 || raw.Mask&unix.IN_MOVED_FROM != 0:
				action = "removed"
			default:
				continue
			}
			logEvent(db, "file", filepath.Base(dir), fmt.Sprintf("File %s: %s", action, fullPath))
		}
	}
}

func startKeybindTracker(db *sql.DB) {
	logInfo("Initializing Hyprland keybind tracker...")
	// Fetch keybinds periodically
	go refreshBindsLoop()

	var activeReaders = make(map[string]bool)
	var readersMutex sync.Mutex
	eventChan := make(chan inputEvent, 100)

	for {
		devices, err := findKeyboardDevices()
		if err == nil {
			readersMutex.Lock()
			for _, devPath := range devices {
				if !activeReaders[devPath] {
					activeReaders[devPath] = true
					go func(path string) {
						defer func() {
							readersMutex.Lock()
							delete(activeReaders, path)
							readersMutex.Unlock()
						}()
						readInputEvents(path, eventChan)
					}(devPath)
				}
			}
			readersMutex.Unlock()
		}

		select {
		case ev := <-eventChan:
			handleInputEvent(db, ev)
		case <-time.After(10 * time.Second):
			// Periodically loop to scan for newly connected keyboards
		}
	}
}

func refreshBindsLoop() {
	for {
		refreshBinds()
		time.Sleep(60 * time.Second)
	}
}

func refreshBinds() {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "hyprctl", "binds", "-j")
	var out bytes.Buffer
	cmd.Stdout = &out
	err := cmd.Run()
	if err != nil {
		logError("Failed to fetch Hyprland binds: %v", err)
		return
	}

	var binds []HyprBind
	if err := json.Unmarshal(out.Bytes(), &binds); err != nil {
		logError("Failed to parse Hyprland binds JSON: %v", err)
		return
	}

	bindsMutex.Lock()
	cachedBinds = binds
	bindsMutex.Unlock()
}

func findKeyboardDevices() ([]string, error) {
	var kbdDevices []string
	baseDir := "/dev/input/by-path"
	files, err := os.ReadDir(baseDir)
	if err == nil {
		for _, f := range files {
			if strings.HasSuffix(f.Name(), "-event-kbd") {
				kbdDevices = append(kbdDevices, filepath.Join(baseDir, f.Name()))
			}
		}
	}
	// Fallback to active event files if /dev/input/by-path is not set up
	if len(kbdDevices) == 0 {
		files, err := os.ReadDir("/dev/input")
		if err == nil {
			for _, f := range files {
				if strings.HasPrefix(f.Name(), "event") {
					devPath := filepath.Join("/dev/input", f.Name())
					kbdDevices = append(kbdDevices, devPath)
				}
			}
		}
	}
	return kbdDevices, nil
}

func readInputEvents(devPath string, eventChan chan inputEvent) {
	file, err := os.Open(devPath)
	if err != nil {
		return
	}
	defer file.Close()

	buf := make([]byte, 24)
	for {
		n, err := file.Read(buf)
		if err != nil {
			return
		}
		if n == 24 {
			var ev inputEvent
			ev.TimeSec = int64(binary.LittleEndian.Uint64(buf[0:8]))
			ev.TimeUsec = int64(binary.LittleEndian.Uint64(buf[8:16]))
			ev.Type = binary.LittleEndian.Uint16(buf[16:18])
			ev.Code = binary.LittleEndian.Uint16(buf[18:20])
			ev.Value = int32(binary.LittleEndian.Uint32(buf[20:24]))
			eventChan <- ev
		}
	}
}

func isModifier(code uint16) bool {
	switch code {
	case 42, 54, 29, 97, 56, 100, 125, 126, 58:
		return true
	}
	return false
}

func getModifierBit(code uint16) int {
	switch code {
	case 42, 54: // Shift
		return 1
	case 58: // Caps Lock
		return 2
	case 29, 97: // Control
		return 4
	case 56, 100: // Alt
		return 8
	case 125, 126: // Super
		return 64
	}
	return 0
}

func handleInputEvent(db *sql.DB, ev inputEvent) {
	if ev.Type != 1 {
		return
	}
	if isModifier(ev.Code) {
		if ev.Code == 58 {
			return
		} // Caps Lock is lock-state, not inferred from key presses.
		if ev.Value == 1 {
			pressedModifiers[ev.Code] = true
		} else if ev.Value == 0 {
			delete(pressedModifiers, ev.Code)
		}
		activeModifiers = 0
		for code := range pressedModifiers {
			activeModifiers |= getModifierBit(code)
		}
	} else if ev.Value == 1 {
		if key := translateKeycode(ev.Code); key != "" {
			matchAndLogKeybind(db, activeModifiers, key)
		}
	}
}

func matchAndLogKeybind(db *sql.DB, modmask int, key string) {
	bindsMutex.RLock()
	defer bindsMutex.RUnlock()

	for _, bind := range cachedBinds {
		if bind.Modmask == modmask && strings.ToLower(bind.Key) == key {
			keybindText := getFriendlyKeybindText(modmask, key)
			actionText := bind.Dispatcher
			if bind.Arg != "" {
				actionText += " " + bind.Arg
			}
			logText := fmt.Sprintf("Keybind triggered: %s -> %s", keybindText, actionText)

			logInfo("Keybind matched")
			logEvent(db, "keybind", "hyprland", logText)
			break
		}
	}
}

func getFriendlyKeybindText(modmask int, key string) string {
	var parts []string
	if (modmask & 64) != 0 {
		parts = append(parts, "SUPER")
	}
	if (modmask & 4) != 0 {
		parts = append(parts, "CTRL")
	}
	if (modmask & 8) != 0 {
		parts = append(parts, "ALT")
	}
	if (modmask & 1) != 0 {
		parts = append(parts, "SHIFT")
	}
	parts = append(parts, strings.ToUpper(key))
	return strings.Join(parts, " + ")
}

func sendKeybindNotification(keybind, dispatcher, arg string) {
	actionText := dispatcher
	if arg != "" {
		actionText += " " + arg
	}
	// Trigger desktop notification routed to swaync
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "notify-send", "-a", "Qareen", "-i", "keyboard", keybind, "Action: "+actionText)
	cmd.Run()
}

func translateKeycode(code uint16) string {
	switch code {
	case 1:
		return "escape"
	case 2:
		return "1"
	case 3:
		return "2"
	case 4:
		return "3"
	case 5:
		return "4"
	case 6:
		return "5"
	case 7:
		return "6"
	case 8:
		return "7"
	case 9:
		return "8"
	case 10:
		return "9"
	case 11:
		return "0"
	case 12:
		return "minus"
	case 13:
		return "equal"
	case 14:
		return "backspace"
	case 15:
		return "tab"
	case 16:
		return "q"
	case 17:
		return "w"
	case 18:
		return "e"
	case 19:
		return "r"
	case 20:
		return "t"
	case 21:
		return "y"
	case 22:
		return "u"
	case 23:
		return "i"
	case 24:
		return "o"
	case 25:
		return "p"
	case 26:
		return "bracketleft"
	case 27:
		return "bracketright"
	case 28:
		return "enter"
	case 30:
		return "a"
	case 31:
		return "s"
	case 32:
		return "d"
	case 33:
		return "f"
	case 34:
		return "g"
	case 35:
		return "h"
	case 36:
		return "j"
	case 37:
		return "k"
	case 38:
		return "l"
	case 39:
		return "semicolon"
	case 40:
		return "apostrophe"
	case 41:
		return "grave"
	case 43:
		return "backslash"
	case 44:
		return "z"
	case 45:
		return "x"
	case 46:
		return "c"
	case 47:
		return "v"
	case 48:
		return "b"
	case 49:
		return "n"
	case 50:
		return "m"
	case 51:
		return "comma"
	case 52:
		return "period"
	case 53:
		return "slash"
	case 57:
		return "space"
	case 59:
		return "f1"
	case 60:
		return "f2"
	case 61:
		return "f3"
	case 62:
		return "f4"
	case 63:
		return "f5"
	case 64:
		return "f6"
	case 65:
		return "f7"
	case 66:
		return "f8"
	case 67:
		return "f9"
	case 68:
		return "f10"
	case 87:
		return "f11"
	case 88:
		return "f12"
	case 103:
		return "up"
	case 105:
		return "left"
	case 106:
		return "right"
	case 108:
		return "down"
	case 113:
		return "xf86audiosplay"
	case 114:
		return "xf86audiolowervolume"
	case 115:
		return "xf86audioraisevolume"
	case 163:
		return "xf86audionext"
	case 164:
		return "xf86audioplay"
	case 165:
		return "xf86audioprev"
	case 166:
		return "xf86audiostop"
	case 224:
		return "xf86monbrightnessdown"
	case 225:
		return "xf86monbrightnessup"
	}
	return ""
}

// ---------------- WEB DASHBOARD ----------------

func serveDashboard() {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/", proxyAPI)
	sub, _ := fs.Sub(dashboardFS, "dashboard")
	mux.Handle("/", http.FileServer(http.FS(sub)))
	mux.Handle("/dashboard/", http.StripPrefix("/dashboard/", http.FileServer(http.FS(sub))))
	secure := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != "127.0.0.1:2847" && r.Host != "localhost:2847" {
			http.Error(w, "Invalid host", 403)
			return
		}
		w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Cache-Control", "no-store")
		mux.ServeHTTP(w, r)
	})
	fmt.Println("Dashboard: http://127.0.0.1:2847 (enter the token from qareen token admin)")
	server := &http.Server{Addr: "127.0.0.1:2847", Handler: secure, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 90 * time.Second, IdleTimeout: 30 * time.Second}
	if err := server.ListenAndServe(); err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
}
