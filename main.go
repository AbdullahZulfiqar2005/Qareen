package main

import (
	"bufio"
	"bytes"
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	_ "github.com/glebarez/go-sqlite"
)

// Global Configuration
const (
	ServerPort = "2846"
	ServerURL  = "http://127.0.0.1:" + ServerPort
	GroqModel  = "llama-3.3-70b-versatile"
	EmbedDim   = 384 // output dimension of the all-MiniLM-L6-v2 sentence embedding model
)

// ANSI Colors
const (
	Blue   = "\033[94m"
	Green  = "\033[92m"
	Yellow = "\033[93m"
	Red    = "\033[91m"
	Bold   = "\033[1m"
	Reset  = "\033[0m"
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

func main() {
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

	// 1. Start Python embedding server if port is not active
	if !isPortOpen(ServerPort) {
		fmt.Println("Launching local embedding & tab server...")
		pyBin := filepath.Join(os.Getenv("HOME"), "venv/bin/python")
		pyCmd := exec.Command(pyBin, "embedding_server.py")
		pyCmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}

		// Redirect output to log file
		lf, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if err == nil {
			pyCmd.Stdout = lf
			pyCmd.Stderr = lf
		}

		err = pyCmd.Start()
		if err != nil {
			fmt.Printf("Error starting python embedding server: %v\n", err)
			return
		}

		// Write Python PID
		os.WriteFile(pyPidPath, []byte(strconv.Itoa(pyCmd.Process.Pid)), 0644)

		// Wait for port to open (up to 10 seconds)
		for i := 0; i < 20; i++ {
			if isPortOpen(ServerPort) {
				break
			}
			time.Sleep(500 * time.Millisecond)
		}
	}

	// 2. Start Go Daemon process
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

	// Kill Go Daemon
	if isPidRunning(daemonPidPath) {
		pidData, _ := os.ReadFile(daemonPidPath)
		pid, _ := strconv.Atoi(string(bytes.TrimSpace(pidData)))
		fmt.Printf("Stopping Qareen daemon (PID %d)...\n", pid)
		syscall.Kill(pid, syscall.SIGTERM)
		os.Remove(daemonPidPath)
		stoppedAny = true
	}

	// Kill Python Server
	if isPidRunning(pyPidPath) {
		pidData, _ := os.ReadFile(pyPidPath)
		pid, _ := strconv.Atoi(string(bytes.TrimSpace(pidData)))
		fmt.Printf("Stopping embedding server (PID %d)...\n", pid)
		syscall.Kill(pid, syscall.SIGTERM)
		os.Remove(pyPidPath)
		stoppedAny = true
	}

	if stoppedAny {
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
	dbPath, _, _, _ := getPaths()

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

	// 1. Fetch embedding of query
	qEmbed, err := getEmbedding(queryText)
	if err != nil {
		fmt.Printf("%sError: Failed to embed query using local server (%v). Make sure service is running.%s\n", Red, err, Reset)
		return
	}

	// 2. Open DB
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		fmt.Printf("Error opening SQLite: %v\n", err)
		return
	}
	defer db.Close()

	// 3. Scan database and calculate similarity
	rows, err := db.Query("SELECT id, timestamp, event_type, app, content, embedding FROM events")
	if err != nil {
		fmt.Printf("Error querying events: %v\n", err)
		return
	}
	defer rows.Close()

	var matches []SimilarityMatch
	for rows.Next() {
		var id int
		var timestamp, eventType, app, content string
		var embedRaw []byte
		if err := rows.Scan(&id, &timestamp, &eventType, &app, &content, &embedRaw); err == nil {
			embedding, ok := decodeEmbedding(embedRaw)
			if ok && len(embedding) == EmbedDim {
				score := cosineSimilarity(qEmbed, embedding)
				matches = append(matches, SimilarityMatch{
					Event: Event{
						ID:        id,
						Timestamp: timestamp,
						Type:      eventType,
						App:       app,
						Content:   content,
					},
					Score: score,
				})
			}
		}
	}

	if len(matches) == 0 {
		fmt.Println("No recorded interactions or events found. Try running some shell commands or browsing to build history!")
		return
	}

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

	fmt.Printf("\n%s%s🕵️‍♂️ Qareen: Scanning your memory timeline...%s\n", Blue, Bold, Reset)
	fmt.Printf("%s[Timeline Context Retrieved (Top Semantic Matches)]%s\n", Yellow, Reset)
	fmt.Println(timeline)
	fmt.Printf("%s==================================================%s\n", Blue, Reset)
	fmt.Printf("%s🧠 Generating guidance from your digital twin...%s\n\n", Green, Reset)

	// Stream guidance response from Groq
	err = queryGroqLLM(queryText, timeline)
	if err != nil {
		fmt.Printf("\n%sError querying LLM: %v%s\n", Red, err, Reset)
	} else {
		// Log successful query as a memory
		logEvent(db, "query", "qareen", "User queried: "+queryText)
	}
}

func queryGroqLLM(query, timeline string) error {
	apiKey := os.Getenv("GROQ_API_KEY")
	if apiKey == "" {
		return fmt.Errorf("GROQ_API_KEY environment variable is not set. Export it (e.g. in ~/.zshrc or ~/.bashrc) before running qareen: export GROQ_API_KEY=\"your-key-here\"")
	}

	systemContent := "You are Qareen, the user's digital twin assistant. Your role is to guide the user on how they previously resolved an issue, based on their logged activities (terminal commands, active window titles, Firefox search/history/tabs, system errors). Analyze the context timeline provided. If they ran specific commands, visited stackoverflow, or fixed config files, summarize the exact steps they took so they can quickly fix it again."
	userContent := fmt.Sprintf("User Query: %s\n\nRelevant Interaction Context (Timeline):\n%s", query, timeline)

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
					fmt.Print(chunk.Choices[0].Delta.Content)
				}
			}
		}
	}
	fmt.Println()
	return nil
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
