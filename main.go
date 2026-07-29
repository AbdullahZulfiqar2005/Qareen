package main

import (
	"bufio"
	"bytes"
	"database/sql"
	"embed"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"math"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"runtime/debug"
	"time"
	"unsafe"

	"golang.org/x/sys/unix"

	_ "github.com/glebarez/go-sqlite"
)

// Global Configuration
const (
	ServerPort     = "2846"
	ServerURL      = "http://127.0.0.1:" + ServerPort
	DashboardPort  = "2847"
	GroqModel      = "llama-3.3-70b-versatile"
	EmbedDim       = 384 // output dimension of the all-MiniLM-L6-v2 sentence embedding model
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
	debug.SetGCPercent(20)
	loadDotEnv()

	if len(os.Args) < 2 {
		printUsage()
		return
	}

	command := os.Args[1]
	switch command {
	case "start":
		startDaemon()
	case "stop":
		stopDaemon()
	case "status":
		statusDaemon()
	case "run-daemon":
		runDaemonLoop()
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
	dbPath := filepath.Join(home, ".qareen.db")
	daemonPidPath := "/tmp/qareend.pid"
	pyPidPath := "/tmp/qareen_py.pid"
	logPath := filepath.Join(home, ".qareen.log")
	return dbPath, daemonPidPath, pyPidPath, logPath
}

func startDaemon() {
	_, daemonPidPath, pyPidPath, logPath := getPaths()

	// Check if already running
	if isPidRunning(daemonPidPath) {
		fmt.Println("Qareen background service is already running.")
		return
	}

	fmt.Println("Starting Qareen system tracking service...")

	// 1. Start Python embedding server if port is not active.
	//
	// IMPORTANT: a failure here must NOT stop step 2 below. Previously this
	// function returned early on any embedding-server failure (missing venv,
	// python crash, etc.), which silently prevented ALL perception - shell
	// commands, active window, browser tabs, system logs, everything - from
	// being tracked, not just AI-query/embedding support. logEvent() already
	// degrades gracefully (saves events with a NULL embedding) when the
	// embedding server is unreachable, so the tracking daemon should always
	// start; we just warn clearly that embeddings/qareen query won't work
	// until the embedding server issue is fixed.
	embeddingServerOK := isPortOpen(ServerPort)
	if !embeddingServerOK {
		fmt.Println("Launching local embedding & tab server...")
		pyBin := filepath.Join(os.Getenv("HOME"), "venv/bin/python")

		pyScript := "embedding_server.py"
		if _, err := os.Stat(pyScript); os.IsNotExist(err) {
			workspaceScript := filepath.Join(os.Getenv("HOME"), "Qareen", "embedding_server.py")
			if _, err := os.Stat(workspaceScript); err == nil {
				pyScript = workspaceScript
			}
		}

		pyCmd := exec.Command(pyBin, pyScript)
		pyCmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}

		// Redirect output to log file
		lf, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if err == nil {
			pyCmd.Stdout = lf
			pyCmd.Stderr = lf
		}

		err = pyCmd.Start()
		if err != nil {
			fmt.Printf("%sWarning: failed to start python embedding server: %v%s\n", Yellow, err, Reset)
			fmt.Println("Tracking will continue, but embeddings, 'qareen query', and browser-extension logging will not work until this is fixed.")
			fmt.Printf("Check that %s exists (see README setup step 1), or that %s is available on PATH.\n", pyBin, pyScript)
		} else {
			// Write Python PID
			os.WriteFile(pyPidPath, []byte(strconv.Itoa(pyCmd.Process.Pid)), 0644)

			// Wait for port to open (up to 10 seconds)
			for i := 0; i < 20; i++ {
				if isPortOpen(ServerPort) {
					embeddingServerOK = true
					break
				}
				time.Sleep(500 * time.Millisecond)
			}
			if !embeddingServerOK {
				fmt.Printf("%sWarning: embedding server did not come up within 10s. Tracking will continue without it.%s\n", Yellow, Reset)
			}
		}
	}

	// 2. Start Go Daemon process (always - independent of step 1's outcome)
	cmd := exec.Command(os.Args[0], "run-daemon")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	lf, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err == nil {
		cmd.Stdout = lf
		cmd.Stderr = lf
	}
	err = cmd.Start()
	if err != nil {
		fmt.Printf("Error launching Qareen background daemon: %v\n", err)
		return
	}

	os.WriteFile(daemonPidPath, []byte(strconv.Itoa(cmd.Process.Pid)), 0644)
	fmt.Printf("%sQareen background service successfully started!%s\n", Green, Reset)
	fmt.Printf("Logs are stored at: %s\n", logPath)
}

func stopDaemon() {
	_, daemonPidPath, pyPidPath, _ := getPaths()

	stoppedAny := false
	var daemonPid, pyPid int

	// Kill Go Daemon
	if isPidRunning(daemonPidPath) {
		pidData, _ := os.ReadFile(daemonPidPath)
		daemonPid, _ = strconv.Atoi(string(bytes.TrimSpace(pidData)))
		fmt.Printf("Stopping Qareen daemon (PID %d)...\n", daemonPid)
		syscall.Kill(daemonPid, syscall.SIGTERM)
		stoppedAny = true
	}

	// Kill Python Server
	if isPidRunning(pyPidPath) {
		pidData, _ := os.ReadFile(pyPidPath)
		pyPid, _ = strconv.Atoi(string(bytes.TrimSpace(pidData)))
		fmt.Printf("Stopping embedding server (PID %d)...\n", pyPid)
		syscall.Kill(pyPid, syscall.SIGTERM)
		stoppedAny = true
	}

	if stoppedAny {
		// Wait for processes to actually stop and free resources/ports
		fmt.Print("Waiting for services to release resources...")
		for i := 0; i < 30; i++ {
			daemonRunning := false
			if daemonPid > 0 {
				if proc, err := os.FindProcess(daemonPid); err == nil {
					if err := proc.Signal(syscall.Signal(0)); err == nil {
						daemonRunning = true
					}
				}
			}
			pyRunning := false
			if pyPid > 0 {
				if proc, err := os.FindProcess(pyPid); err == nil {
					if err := proc.Signal(syscall.Signal(0)); err == nil {
						pyRunning = true
					}
				}
			}
			if !daemonRunning && !pyRunning && !isPortOpen(ServerPort) {
				break
			}
			fmt.Print(".")
			time.Sleep(200 * time.Millisecond)
		}
		fmt.Println()

		// Clean up pid files at the end
		os.Remove(daemonPidPath)
		os.Remove(pyPidPath)

		fmt.Printf("%sQareen background services stopped.%s\n", Yellow, Reset)
	} else {
		fmt.Println("No active Qareen services found running.")
	}
}

func statusDaemon() {
	dbPath, daemonPidPath, pyPidPath, _ := getPaths()

	daemonRunning := isPidRunning(daemonPidPath)
	pyRunning := isPidRunning(pyPidPath)

	fmt.Printf("%s%sQareen Status Report:%s\n", Blue, Bold, Reset)
	if daemonRunning {
		pid, _ := os.ReadFile(daemonPidPath)
		fmt.Printf("  - Tracking Daemon:  %sActive%s (PID %s)\n", Green, Reset, string(bytes.TrimSpace(pid)))
	} else {
		fmt.Printf("  - Tracking Daemon:  %sInactive%s\n", Red, Reset)
	}

	if pyRunning {
		pid, _ := os.ReadFile(pyPidPath)
		fmt.Printf("  - Embedding Server: %sActive%s (PID %s)\n", Green, Reset, string(bytes.TrimSpace(pid)))
	} else {
		fmt.Printf("  - Embedding Server: %sInactive%s\n", Red, Reset)
	}

	// Database info
	db, err := sql.Open("sqlite", dbPath)
	if err == nil {
		defer db.Close()
		var count int
		err = db.QueryRow("SELECT COUNT(*) FROM events").Scan(&count)
		if err == nil {
			fmt.Printf("  - SQLite Database:  Connected (%d events logged)\n", count)
		} else {
			fmt.Println("  - SQLite Database:  Connected (empty or uninitialized)")
		}
	} else {
		fmt.Printf("  - SQLite Database:  %sDisconnected (%v)%s\n", Red, err, Reset)
	}
}

// ---------------- DAEMON RUN LOOP ----------------

func runDaemonLoop() {
	dbPath, daemonPidPath, _, _ := getPaths()
	os.WriteFile(daemonPidPath, []byte(strconv.Itoa(os.Getpid())), 0644)
	defer os.Remove(daemonPidPath)

	// Setup database
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		logError("Failed to open SQLite database: %v", err)
		return
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
		return
	}
	// Helpful for `qareen list` / status and any future timestamp-ordered
	// queries; without it every ORDER BY/LIMIT still does a full table scan.
	_, err = db.Exec(`CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)`)
	if err != nil {
		logError("Failed to create timestamp index: %v", err)
	}

	logInfo("Daemon loop started.")

	// Start native Hyprland keybind tracker
	go startKeybindTracker(db)

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
	lastPlacesTimestamp := time.Now().UnixNano() / 1000 // UTC microseconds

	// Firefox Tabs state
	var lastTabs []string

	// System log tracking state
	lastSysLogCheck := time.Now()

	// Loop tickers
	tickerFast := time.NewTicker(2 * time.Second)    // Active Window
	tickerMedium := time.NewTicker(5 * time.Second)  // Shell Command / Firefox history
	tickerSlow := time.NewTicker(30 * time.Second)   // Open Tabs
	tickerSysLog := time.NewTicker(15 * time.Second) // Journalctl errors

	defer tickerFast.Stop()
	defer tickerMedium.Stop()
	defer tickerSlow.Stop()
	defer tickerSysLog.Stop()

	for {
		select {
		case <-done:
			logInfo("Daemon loop exited.")
			return
		case <-tickerFast.C:
			// 1. Active window tracking
			c, t, err := getActiveWindow()
			if err == nil && (c != lastClass || t != lastTitle) {
				if c != "" || t != "" {
					appType := c
					logContent := t
					lowerClass := strings.ToLower(c)
					isBrowser := strings.Contains(lowerClass, "firefox") ||
						strings.Contains(lowerClass, "chrome") ||
						strings.Contains(lowerClass, "brave") ||
						strings.Contains(lowerClass, "chromium")

					if isBrowser {
						if strings.Contains(t, "WhatsApp") {
							appType = "whatsapp"
							if strings.Contains(t, " - ") {
								parts := strings.SplitN(t, " - ", 2)
								logContent = "Chatting with " + parts[1] + " on WhatsApp"
							} else {
								logContent = "Opened WhatsApp Web"
							}
						} else if strings.Contains(t, "ChatGPT") {
							appType = "chatgpt"
							if strings.Contains(t, " - ") {
								parts := strings.SplitN(t, " - ", 2)
								logContent = "Prompted/Viewed ChatGPT: " + parts[1]
							} else {
								logContent = "Opened ChatGPT"
							}
						} else if strings.Contains(t, "Claude") {
							appType = "claude"
							if strings.Contains(t, " - ") {
								parts := strings.SplitN(t, " - ", 2)
								logContent = "Prompted/Viewed Claude: " + parts[1]
							} else {
								logContent = "Opened Claude"
							}
						} else if strings.Contains(t, "Gemini") {
							appType = "gemini"
							if strings.Contains(t, " - ") {
								parts := strings.SplitN(t, " - ", 2)
								logContent = "Prompted/Viewed Gemini: " + parts[1]
							} else {
								logContent = "Opened Gemini"
							}
						} else if strings.Contains(t, "Grok") {
							appType = "grok"
							if strings.Contains(t, " - ") {
								parts := strings.SplitN(t, " - ", 2)
								logContent = "Prompted/Viewed Grok: " + parts[1]
							} else {
								logContent = "Opened Grok"
							}
						} else if strings.Contains(t, "Google Search") {
							appType = "google"
							if strings.Contains(t, " - ") {
								parts := strings.SplitN(t, " - ", 2)
								logContent = "Searched Google for: " + parts[0]
							}
						} else {
							logContent = fmt.Sprintf("Browsed: %s", t)
						}
					} else {
						logContent = fmt.Sprintf("Active window changed to: %s (app: %s)", t, c)
					}

					logEvent(db, "window", appType, logContent)
				}
				lastClass = c
				lastTitle = t
			}

		case <-tickerMedium.C:
			// 2. Shell Command tracking (Zsh)
			if fi, err := os.Stat(zshPath); err == nil && fi.Size() > lastZshSize {
				f, err := os.Open(zshPath)
				if err == nil {
					f.Seek(lastZshSize, 0)
					scanner := bufio.NewScanner(f)
					for scanner.Scan() {
						line := scanner.Text()
						if strings.HasPrefix(line, ": ") {
							parts := strings.SplitN(line, ";", 2)
							if len(parts) == 2 {
								cmdStr := strings.TrimSpace(parts[1])
								if cmdStr != "" {
									logEvent(db, "command", "zsh", fmt.Sprintf("Executed shell command: %s", cmdStr))
								}
							}
						}
					}
					f.Close()
				}
				lastZshSize = fi.Size()
			}

			// Shell Command tracking (Bash)
			if fi, err := os.Stat(bashPath); err == nil && fi.Size() > lastBashSize {
				f, err := os.Open(bashPath)
				if err == nil {
					f.Seek(lastBashSize, 0)
					scanner := bufio.NewScanner(f)
					for scanner.Scan() {
						cmdStr := strings.TrimSpace(scanner.Text())
						if cmdStr != "" {
							logEvent(db, "command", "bash", fmt.Sprintf("Executed shell command: %s", cmdStr))
						}
					}
					f.Close()
				}
				lastBashSize = fi.Size()
			}

			// 3. Firefox history tracking.
			// Previously this copied the *entire* places.sqlite file (which can be
			// tens of megabytes) to /tmp every 5 seconds forever, burning disk I/O,
			// CPU, and SSD write cycles for no reason. Firefox's places.sqlite uses
			// WAL journal mode, so it can be opened directly as a second read-only
			// connection with no copy needed. We only fall back to the old
			// copy-then-read approach if the direct read-only open fails (e.g. the
			// profile is using a legacy rollback journal).
			ffProfile, err := findFirefoxProfile()
			if err == nil {
				placesPath := filepath.Join(ffProfile, "places.sqlite")
				latestVisit, ok := scanFirefoxHistory(placesPath, lastPlacesTimestamp, db)
				if !ok {
					// Fallback: copy then read (slow path, rarely needed).
					tempPlaces := "/tmp/qareen_places.sqlite"
					if err := copyFile(placesPath, tempPlaces); err == nil {
						latestVisit, _ = scanFirefoxHistory(tempPlaces, lastPlacesTimestamp, db)
						os.Remove(tempPlaces)
					}
				}
				if latestVisit > lastPlacesTimestamp {
					lastPlacesTimestamp = latestVisit
				}
			}

		case <-tickerSlow.C:
			// 4. Firefox Tabs tracking
			tabs, err := getFirefoxTabs()
			if err == nil && len(tabs) > 0 {
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
						logEvent(db, "tab", "firefox", fmt.Sprintf("Opened browser tab: %s", tabStr))
					}
				}
				lastTabs = currentTabStrings
			}
			debug.FreeOSMemory()

		case <-tickerSysLog.C:
			// 5. System Error Logs tracking.
			// Previously this always asked journalctl for "the last 15 seconds",
			// with no relation to when it last actually ran. Timer drift (GC
			// pauses, system load, laptop suspend) meant errors could be logged
			// twice or missed entirely. We now track the exact timestamp of the
			// last successful scan and ask journalctl for everything since then.
			errs, err := getJournalErrors(lastSysLogCheck)
			if err == nil {
				for _, errMsg := range errs {
					logEvent(db, "error", "system", fmt.Sprintf("System logged critical error: %s", errMsg))
				}
			}
			lastSysLogCheck = time.Now()
		}
	}
}

func getActiveWindow() (string, string, error) {
	cmd := exec.Command("hyprctl", "activewindow", "-j")
	var out bytes.Buffer
	cmd.Stdout = &out
	err := cmd.Run()
	if err != nil {
		return "", "", err
	}
	var res struct {
		Class string `json:"class"`
		Title string `json:"title"`
	}
	if err := json.Unmarshal(out.Bytes(), &res); err != nil {
		return "", "", err
	}
	return res.Class, res.Title, nil
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

func getJournalErrors(since time.Time) ([]string, error) {
	sinceStr := since.Format("2006-01-02 15:04:05")
	cmd := exec.Command("journalctl", "--since", sinceStr, "-p", "3", "-o", "json")
	out, err := cmd.Output()
	if err != nil {
		return nil, err
	}
	var errors []string
	lines := strings.Split(string(out), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var entry map[string]interface{}
		if err := json.Unmarshal([]byte(line), &entry); err == nil {
			if msg, ok := entry["MESSAGE"].(string); ok {
				unit := ""
				if u, ok := entry["_SYSTEMD_UNIT"].(string); ok {
					unit = u
				}
				errors = append(errors, fmt.Sprintf("%s: %s", unit, msg))
			}
		}
	}
	return errors, nil
}

func logEvent(db *sql.DB, eventType, app, content string) {
	timestampStr := time.Now().Format("2006-01-02 15:04:05")
	formattedText := fmt.Sprintf("[%s] [%s] [%s] %s", timestampStr, strings.ToUpper(eventType), app, content)

	logInfo("Logging Event: %s", formattedText)

	// Fetch embedding. If the embedding server is briefly unreachable we still
	// keep the event (with a NULL embedding) instead of silently dropping it -
	// previously a single failed embedding call meant the whole event vanished
	// with no record it ever happened.
	embedding, err := getEmbedding(formattedText)
	var embedBlob []byte
	if err != nil {
		logError("Failed to fetch embedding (event will be saved without one): %v", err)
	} else {
		embedBlob = encodeEmbedding(embedding)
	}

	_, err = db.Exec(
		"INSERT INTO events (timestamp, event_type, app, content, embedding) VALUES (?, ?, ?, ?, ?)",
		timestampStr, eventType, app, content, embedBlob,
	)
	if err != nil {
		logError("Failed to save event to database: %v", err)
	}
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
	if len(raw) > 0 && (raw[0] == '[' || raw[0] == ' ') {
		var legacy []float64
		if err := json.Unmarshal(raw, &legacy); err == nil {
			return legacy, true
		}
		return nil, false
	}
	if len(raw) == 0 || len(raw)%4 != 0 {
		return nil, false
	}
	out := make([]float64, len(raw)/4)
	for i := range out {
		bits := binary.LittleEndian.Uint32(raw[i*4:])
		out[i] = float64(math.Float32frombits(bits))
	}
	return out, true
}

// ---------------- QUERY SEARCH & LLM ----------------

func queryDaemon(queryText string) {
	dbPath, _, _, _ := getPaths()

	// Open DB (needed for logging the query as an event at the end)
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		fmt.Printf("Error opening SQLite: %v\n", err)
		return
	}
	defer db.Close()

	// Semantic search via FAISS index on the embedding server
	searchPayload, _ := json.Marshal(map[string]interface{}{
		"query": queryText,
		"k":     40,
	})
	searchResp, err := http.Post(ServerURL+"/search", "application/json", bytes.NewBuffer(searchPayload))
	if err != nil {
		fmt.Printf("Error calling search endpoint: %v\n", err)
		return
	}
	defer searchResp.Body.Close()
	searchBody, err := io.ReadAll(searchResp.Body)
	if err != nil {
		fmt.Printf("Error reading search response: %v\n", err)
		return
	}

	var searchResult struct {
		Results []struct {
			EventID   int     `json:"event_id"`
			Timestamp string  `json:"timestamp"`
			EventType string  `json:"event_type"`
			App       string  `json:"app"`
			Content   string  `json:"content"`
			Score     float64 `json:"score"`
		} `json:"results"`
	}
	if err := json.Unmarshal(searchBody, &searchResult); err != nil {
		fmt.Printf("Error parsing search response: %v\n", err)
		return
	}

	var matches []SimilarityMatch
	for _, r := range searchResult.Results {
		matches = append(matches, SimilarityMatch{
			Event: Event{
				ID:        r.EventID,
				Timestamp: r.Timestamp,
				Type:      r.EventType,
				App:       r.App,
				Content:   r.Content,
			},
			Score: r.Score,
		})
	}

	if len(matches) == 0 {
		fmt.Println("No recorded interactions or events found. Try running some shell commands or browsing to build history!")
		return
	}

	// Adjust scores and filter matches to prioritize real activities over self-queries/notifications
	queryLower := strings.ToLower(queryText)
	hasQueryKeyword := strings.Contains(queryLower, "query") || strings.Contains(queryLower, "queries") || strings.Contains(queryLower, "ask")
	hasNotificationKeyword := strings.Contains(queryLower, "notification") || strings.Contains(queryLower, "notifications") || strings.Contains(queryLower, "alert") || strings.Contains(queryLower, "permission")

	var filteredMatches []SimilarityMatch
	for i := range matches {
		m := matches[i]
		mType := strings.ToLower(m.Event.Type)
		mApp := strings.ToLower(m.Event.App)
		mContent := strings.ToLower(m.Event.Content)

		// 1. Skip self-queries and system notifications unless explicitly requested
		if mType == "query" && !hasQueryKeyword {
			continue
		}
		if mType == "notification" && !hasNotificationKeyword {
			continue
		}

		score := m.Score

		// 2. Boost exact keyword matches in app name or event type
		for _, word := range strings.Fields(queryLower) {
			if len(word) < 3 {
				continue
			}
			// Boost if the target keyword is the app or event type
			if strings.Contains(mApp, word) || strings.Contains(mType, word) {
				score += 0.40
			}
			// Boost if the content contains the word
			if strings.Contains(mContent, word) {
				score += 0.15
			}
		}

		m.Score = score
		filteredMatches = append(filteredMatches, m)
	}
	matches = filteredMatches

	// Sort matches by cosine score descending
	sort.Slice(matches, func(i, j int) bool {
		return matches[i].Score > matches[j].Score
	})

	// Take top 12 matches
	topLimit := 12
	if len(matches) < topLimit {
		topLimit = len(matches)
	}
	topMatches := matches[:topLimit]

	// Re-sort top matches CHRONOLOGICALLY to build context timeline
	sort.Slice(topMatches, func(i, j int) bool {
		return topMatches[i].Event.Timestamp < topMatches[j].Event.Timestamp
	})

	// Format chronological timeline context
	var timelineBuilder strings.Builder
	for _, m := range topMatches {
		timelineBuilder.WriteString(fmt.Sprintf("[%s] [%s] [%s] %s\n",
			m.Event.Timestamp, strings.ToUpper(m.Event.Type), m.Event.App, m.Event.Content))
	}
	timeline := timelineBuilder.String()

	// 4. Fetch Arch Wiki Reference Context
	var wikiDocs []WikiResult
	wikiResults, err := searchArchWiki(queryText)
	if err == nil {
		wikiDocs = wikiResults
	}

	var wikiBuilder strings.Builder
	if len(wikiDocs) > 0 {
		wikiBuilder.WriteString("\n\nRelevant Arch Wiki Documentation (Reference Fallback):\n")
		for _, doc := range wikiDocs {
			wikiBuilder.WriteString(fmt.Sprintf("--- Source: %s ---\n%s\n\n", doc.Source, doc.Content))
		}
	}
	wikiContext := wikiBuilder.String()

	printStyledTimeline(topMatches)
	fmt.Printf("%s🧠 Generating guidance from your digital twin...%s\n", Green, Reset)

	// Stream guidance response from Groq
	err = queryGroqLLM(queryText, timeline, wikiContext)
	if err != nil {
		fmt.Printf("\n%sError querying LLM: %v%s\n", Red, err, Reset)
	} else {
		// Log successful query as a memory
		logEvent(db, "query", "qareen", "User queried: "+queryText)
	}
}

type WikiResult struct {
	Content string `json:"content"`
	Source  string `json:"source"`
}

func searchArchWiki(query string) ([]WikiResult, error) {
	payload, err := json.Marshal(map[string]interface{}{
		"query": query,
		"k":     3,
	})
	if err != nil {
		return nil, err
	}

	resp, err := http.Post(ServerURL+"/wiki-search", "application/json", bytes.NewBuffer(payload))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("error status %d: %s", resp.StatusCode, string(body))
	}

	var res struct {
		Results []WikiResult `json:"results"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&res); err != nil {
		return nil, err
	}

	return res.Results, nil
}

func queryGroqLLM(query, timeline, wikiContext string) error {
	apiKey := os.Getenv("GROQ_API_KEY")
	if apiKey == "" {
		return fmt.Errorf("GROQ_API_KEY environment variable is not set. Export it (e.g. in ~/.zshrc or ~/.bashrc) before running qareen: export GROQ_API_KEY=\"your-key-here\"")
	}

	systemContent := "You are Qareen, the user's digital twin assistant. Your role is to guide the user on how they previously resolved an issue, based on their logged activities (timeline). If the user's memory timeline does not contain a clear resolution or solution steps to fix the issue, consult the provided Arch Wiki Documentation (Reference Fallback) to formulate and suggest the correct troubleshooting steps."
	userContent := fmt.Sprintf("User Query: %s\n\nRelevant Interaction Context (Timeline):\n%s%s", query, timeline, wikiContext)

	requestBody, err := json.Marshal(map[string]interface{}{
		"model": GroqModel,
		"messages": []map[string]string{
			{"role": "system", "content": systemContent},
			{"role": "user", "content": userContent},
		},
		"temperature": 0.2,
		"stream":      true,
	})
	if err != nil {
		return err
	}

	req, err := http.NewRequest("POST", "https://api.groq.com/openai/v1/chat/completions", bytes.NewBuffer(requestBody))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+apiKey)
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("HTTP error %d: %s", resp.StatusCode, string(body))
	}

	fmt.Printf("\n%s%s┌──────────────────────────────────────────────────────────┐%s\n", Green, Bold, Reset)
	fmt.Printf("%s%s│                 🧠 DIGITAL TWIN GUIDANCE                 │%s\n", Green, Bold, Reset)
	fmt.Printf("%s%s└──────────────────────────────────────────────────────────┘%s\n", Green, Bold, Reset)
	fmt.Print("  ")

	scanner := bufio.NewScanner(resp.Body)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "data: ") {
			dataStr := strings.TrimPrefix(line, "data: ")
			dataStr = strings.TrimSpace(dataStr)
			if dataStr == "[DONE]" {
				break
			}
			var chunk struct {
				Choices []struct {
					Delta struct {
						Content string `json:"content"`
					} `json:"delta"`
				} `json:"choices"`
			}
			if err := json.Unmarshal([]byte(dataStr), &chunk); err == nil {
				if len(chunk.Choices) > 0 {
					content := chunk.Choices[0].Delta.Content
					indented := strings.ReplaceAll(content, "\n", "\n  ")
					fmt.Print(indented)
				}
			}
		}
	}
	fmt.Print("\n\n")
	return nil
}

func printStyledTimeline(topMatches []SimilarityMatch) {
	fmt.Printf("\n%s%s┌──────────────────────────────────────────────────────────┐%s\n", Blue, Bold, Reset)
	fmt.Printf("%s%s│             🗓️  QAREEN DIGITAL TWIN TIMELINE              │%s\n", Blue, Bold, Reset)
	fmt.Printf("%s%s└──────────────────────────────────────────────────────────┘%s\n", Blue, Bold, Reset)
	
	if len(topMatches) == 0 {
		fmt.Printf("  %s(No events recorded in this time window)%s\n", Gray, Reset)
		return
	}

	for i, m := range topMatches {
		tStr := m.Event.Timestamp
		if len(tStr) > 19 {
			tStr = tStr[:19]
		}
		timePart := tStr
		if len(tStr) >= 19 {
			timePart = tStr[11:16]
		}
		
		icon := "🔵"
		typeColor := Cyan
		switch strings.ToLower(m.Event.Type) {
		case "whatsapp":
			icon = "💬"
			typeColor = Green
		case "chatgpt":
			icon = "🤖"
			typeColor = Magenta
		case "command", "history":
			icon = "💻"
			typeColor = Yellow
		case "tab", "browse":
			icon = "🌐"
			typeColor = Blue
		case "gmail":
			icon = "📧"
			typeColor = Red
		case "meet":
			icon = "🎥"
			typeColor = Red
		case "query":
			icon = "🕵️‍♂️"
			typeColor = Cyan
		}

		branch := "├──"
		if i == len(topMatches)-1 {
			branch = "└──"
		}

		appStr := m.Event.App
		if appStr == "" {
			appStr = "system"
		}

		fmt.Printf("  %s%s %s [%s] %s%s%s • %s\n", 
			Gray, branch, icon, timePart, typeColor, strings.ToUpper(m.Event.Type), Reset, appStr)
		
		indentBranch := "  │  "
		if i == len(topMatches)-1 {
			indentBranch = "     "
		}
		
		contentStr := strings.TrimSpace(m.Event.Content)
		if len(contentStr) > 120 {
			contentStr = contentStr[:117] + "..."
		}
		
		fmt.Printf("  %s%s %s%s%s\n", Gray, indentBranch, Reset, contentStr, Reset)
	}
	fmt.Printf("\n")
}

// ---------------- UTILITIES & HELPERS ----------------

func getEmbedding(text string) ([]float64, error) {
	payload, err := json.Marshal(map[string]interface{}{
		"texts": []string{text},
	})
	if err != nil {
		return nil, err
	}

	resp, err := http.Post(ServerURL+"/embed", "application/json", bytes.NewBuffer(payload))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("error status %d: %s", resp.StatusCode, string(body))
	}

	var res struct {
		Embeddings [][]float64 `json:"embeddings"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&res); err != nil {
		return nil, err
	}

	if len(res.Embeddings) == 0 {
		return nil, fmt.Errorf("empty embeddings response")
	}

	return res.Embeddings[0], nil
}

func cosineSimilarity(a, b []float64) float64 {
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
// whether the scan succeeded (false means the caller should fall back to
// copy-then-read).
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
		WHERE h.visit_date > ?
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
			latestVisit = visitDate
			tStr := title.String
			if tStr == "" {
				tStr = url.String
			}
			logEvent(db, "history", "firefox", fmt.Sprintf("Visited webpage: %s (URL: %s)", tStr, url.String))
		}
	}
	return latestVisit, true
}

func findFirefoxProfile() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	baseDir := filepath.Join(home, ".mozilla/firefox")
	files, err := os.ReadDir(baseDir)
	if err != nil {
		return "", err
	}
	var fallbackPath string
	for _, f := range files {
		if f.IsDir() {
			profilePath := filepath.Join(baseDir, f.Name())
			placesPath := filepath.Join(profilePath, "places.sqlite")
			if _, err := os.Stat(placesPath); err == nil {
				if strings.Contains(f.Name(), "default-release") {
					return profilePath, nil
				}
				fallbackPath = profilePath
			}
		}
	}
	if fallbackPath != "" {
		return fallbackPath, nil
	}
	return "", fmt.Errorf("no profile found")
}

func copyFile(src, dst string) error {
	input, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	return os.WriteFile(dst, input, 0644)
}

func isPidRunning(pidFile string) bool {
	pidData, err := os.ReadFile(pidFile)
	if err != nil {
		return false
	}
	pid, err := strconv.Atoi(string(bytes.TrimSpace(pidData)))
	if err != nil {
		return false
	}
	process, err := os.FindProcess(pid)
	if err != nil {
		return false
	}
	// On Unix systems, FindProcess always succeeds. Must send signal 0 to check if alive.
	err = process.Signal(syscall.Signal(0))
	return err == nil
}

func isPortOpen(port string) bool {
	// Native TCP dial instead of shelling out to `nc`, which may not be
	// installed on a minimal system and previously caused isPortOpen to
	// always report false, making `qareen start` spawn duplicate embedding
	// servers on every invocation.
	conn, err := net.DialTimeout("tcp", "127.0.0.1:"+port, 300*time.Millisecond)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

func listEvents(limit int) {
	dbPath, _, _, _ := getPaths()
	db, err := sql.Open("sqlite", dbPath)
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
	dbPath, _, _, _ := getPaths()
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		fmt.Printf("Error: %v\n", err)
		return
	}
	defer db.Close()

	_, err = db.Exec("DELETE FROM events")
	if err == nil {
		fmt.Printf("%sSuccessfully cleared all logged events from database.%s\n", Green, Reset)
	} else {
		fmt.Printf("Error clearing database: %v\n", err)
	}
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
	const flags = unix.IN_CREATE | unix.IN_MOVED_TO | unix.IN_DELETE | unix.IN_MOVED_FROM

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

	if len(watchToDir) == 0 {
		logInfo("No watchable directories found (checked Downloads/Documents/Desktop); file tracking disabled.")
		return
	}

	// Debounce: editors/downloaders often fire several inotify events for
	// the same path within milliseconds of each other (e.g. a temp file then
	// a rename-into-place). Skip repeats of the same path within this window
	// so one save doesn't become three near-identical DB rows.
	lastLogged := make(map[string]time.Time)
	buf := make([]byte, 4096)

	for {
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
			dir := watchToDir[raw.Wd]
			offset += int(unix.SizeofInotifyEvent) + nameLen

			if name == "" || dir == "" {
				continue
			}

			fullPath := filepath.Join(dir, name)
			if t, ok := lastLogged[fullPath]; ok && time.Since(t) < 3*time.Second {
				continue
			}
			lastLogged[fullPath] = time.Now()

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
	cmd := exec.Command("hyprctl", "binds", "-j")
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
	if ev.Type == 1 { // EV_KEY
		if isModifier(ev.Code) {
			if ev.Code == 58 { // Caps Lock
				if ev.Value == 1 { // Toggle on press
					activeModifiers ^= 2
				}
			} else {
				if ev.Value == 1 {
					activeModifiers |= getModifierBit(ev.Code)
				} else if ev.Value == 0 {
					activeModifiers &= ^getModifierBit(ev.Code)
				}
			}
		} else if ev.Value == 1 { // Key press only (no release/repeat)
			keyName := translateKeycode(ev.Code)
			if keyName != "" {
				matchAndLogKeybind(db, activeModifiers, keyName)
			}
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

			logInfo("Keybind Match: %s", logText)
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
	cmd := exec.Command("notify-send", "-a", "Qareen", "-i", "keyboard", keybind, "Action: "+actionText)
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
	dbPath, _, _, _ := getPaths()
	db, err := sql.Open("sqlite", dbPath+"?mode=ro")
	if err != nil {
		fmt.Printf("%sError opening database: %v%s\n", Red, err, Reset)
		return
	}
	defer db.Close()

	mux := http.NewServeMux()
	mux.HandleFunc("/api/events", func(w http.ResponseWriter, r *http.Request) { handleAPIEvents(w, r, db) })
	mux.HandleFunc("/api/search", func(w http.ResponseWriter, r *http.Request) { handleAPISearch(w, r) })
	mux.HandleFunc("/api/stats", func(w http.ResponseWriter, r *http.Request) { handleAPIStats(w, r, db) })

	sub, _ := fs.Sub(dashboardFS, "dashboard")
	mux.Handle("/", http.FileServer(http.FS(sub)))

	fmt.Printf("%s%sQareen Dashboard running at http://localhost:%s%s\n", Green, Bold, DashboardPort, Reset)
	fmt.Printf("Press Ctrl+C to stop.\n")
	if err := http.ListenAndServe("127.0.0.1:"+DashboardPort, mux); err != nil {
		fmt.Printf("%sError starting dashboard: %v%s\n", Red, err, Reset)
	}
}

func jsonResponse(w http.ResponseWriter, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(data)
}

func handleAPIEvents(w http.ResponseWriter, r *http.Request, db *sql.DB) {
	q := r.URL.Query()
	limit, _ := strconv.Atoi(q.Get("limit"))
	if limit <= 0 || limit > 500 {
		limit = 50
	}
	offset, _ := strconv.Atoi(q.Get("offset"))
	if offset < 0 {
		offset = 0
	}
	filterType := q.Get("type")
	filterApp := q.Get("app")

	where := ""
	var args []interface{}
	var conditions []string
	if filterType != "" {
		conditions = append(conditions, "event_type = ?")
		args = append(args, filterType)
	}
	if filterApp != "" {
		conditions = append(conditions, "app = ?")
		args = append(args, filterApp)
	}
	if len(conditions) > 0 {
		where = " WHERE " + strings.Join(conditions, " AND ")
	}

	var total int
	countQuery := "SELECT COUNT(*) FROM events" + where
	db.QueryRow(countQuery, args...).Scan(&total)

	query := "SELECT id, timestamp, event_type, app, content FROM events" + where + " ORDER BY id DESC LIMIT ? OFFSET ?"
	queryArgs := append(args, limit, offset)
	rows, err := db.Query(query, queryArgs...)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	defer rows.Close()

	type EventJSON struct {
		ID        int    `json:"id"`
		Timestamp string `json:"timestamp"`
		EventType string `json:"event_type"`
		App       string `json:"app"`
		Content   string `json:"content"`
	}
	var events []EventJSON
	for rows.Next() {
		var ev EventJSON
		if rows.Scan(&ev.ID, &ev.Timestamp, &ev.EventType, &ev.App, &ev.Content) == nil {
			events = append(events, ev)
		}
	}
	if events == nil {
		events = []EventJSON{}
	}
	jsonResponse(w, map[string]interface{}{"events": events, "total": total})
}

func handleAPISearch(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("q")
	if q == "" {
		http.Error(w, "missing query parameter 'q'", 400)
		return
	}
	k := 12
	if kStr := r.URL.Query().Get("k"); kStr != "" {
		if kVal, err := strconv.Atoi(kStr); err == nil && kVal > 0 {
			k = kVal
		}
	}

	payload, _ := json.Marshal(map[string]interface{}{"query": q, "k": k})
	resp, err := http.Post(ServerURL+"/search", "application/json", bytes.NewBuffer(payload))
	if err != nil {
		http.Error(w, "search service unavailable: "+err.Error(), 502)
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	w.Header().Set("Content-Type", "application/json")
	w.Write(body)
}

func handleAPIStats(w http.ResponseWriter, r *http.Request, db *sql.DB) {
	type LabelCount struct {
		Label string `json:"label"`
		Count int    `json:"count"`
	}
	type HourCount struct {
		Hour  int `json:"hour"`
		Count int `json:"count"`
	}
	type DayCount struct {
		Date  string `json:"date"`
		Count int    `json:"count"`
	}

	var total, today, thisWeek, uniqueApps int
	db.QueryRow("SELECT COUNT(*) FROM events").Scan(&total)
	db.QueryRow("SELECT COUNT(*) FROM events WHERE timestamp >= date('now')").Scan(&today)
	db.QueryRow("SELECT COUNT(*) FROM events WHERE timestamp >= date('now', '-7 days')").Scan(&thisWeek)
	db.QueryRow("SELECT COUNT(DISTINCT app) FROM events").Scan(&uniqueApps)

	var byType []LabelCount
	rows, _ := db.Query("SELECT event_type, COUNT(*) as cnt FROM events GROUP BY event_type ORDER BY cnt DESC")
	if rows != nil {
		defer rows.Close()
		for rows.Next() {
			var lc LabelCount
			if rows.Scan(&lc.Label, &lc.Count) == nil {
				byType = append(byType, lc)
			}
		}
	}

	var topApps []LabelCount
	rows2, _ := db.Query("SELECT app, COUNT(*) as cnt FROM events GROUP BY app ORDER BY cnt DESC LIMIT 20")
	if rows2 != nil {
		defer rows2.Close()
		for rows2.Next() {
			var lc LabelCount
			if rows2.Scan(&lc.Label, &lc.Count) == nil {
				topApps = append(topApps, lc)
			}
		}
	}

	byHour := make([]HourCount, 24)
	for i := range byHour {
		byHour[i].Hour = i
	}
	rows3, _ := db.Query("SELECT CAST(strftime('%H', timestamp) AS INTEGER) as h, COUNT(*) FROM events GROUP BY h ORDER BY h")
	if rows3 != nil {
		defer rows3.Close()
		for rows3.Next() {
			var h, c int
			if rows3.Scan(&h, &c) == nil && h >= 0 && h < 24 {
				byHour[h].Count = c
			}
		}
	}

	var byDay []DayCount
	rows4, _ := db.Query("SELECT date(timestamp) as d, COUNT(*) FROM events WHERE timestamp >= date('now', '-30 days') GROUP BY d ORDER BY d")
	if rows4 != nil {
		defer rows4.Close()
		for rows4.Next() {
			var dc DayCount
			if rows4.Scan(&dc.Date, &dc.Count) == nil {
				byDay = append(byDay, dc)
			}
		}
	}

	if byType == nil {
		byType = []LabelCount{}
	}
	if topApps == nil {
		topApps = []LabelCount{}
	}
	if byDay == nil {
		byDay = []DayCount{}
	}

	jsonResponse(w, map[string]interface{}{
		"total":       total,
		"today":       today,
		"this_week":   thisWeek,
		"unique_apps": uniqueApps,
		"by_type":     byType,
		"top_apps":    topApps,
		"by_hour":     byHour,
		"by_day":      byDay,
	})
}
