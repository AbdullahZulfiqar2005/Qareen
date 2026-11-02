package main

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func qareenDataDir() string {
	dir := os.Getenv("QAREEN_DATA_DIR")
	if dir == "" {
		home, _ := os.UserHomeDir()
		dir = filepath.Join(home, ".local/share/qareen")
	}
	os.MkdirAll(dir, 0700)
	os.Chmod(dir, 0700)
	return dir
}
func apiToken() string {
	data, _ := os.ReadFile(filepath.Join(qareenDataDir(), "admin.token"))
	return strings.TrimSpace(string(data))
}

type localAuthTransport struct{}

func (localAuthTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	r = r.Clone(r.Context())
	if r.URL.Host == "127.0.0.1:2846" {
		r.Header.Set("Authorization", "Bearer "+apiToken())
	}
	return http.DefaultTransport.RoundTrip(r)
}
func init() {
	http.DefaultClient = &http.Client{Timeout: 60 * time.Second, Transport: localAuthTransport{}}
}
func callAPI(method, path string, payload interface{}, result interface{}) error {
	var body io.Reader
	if payload != nil {
		raw, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, ServerURL+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+apiToken())
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	data, err := io.ReadAll(io.LimitReader(res.Body, 16*1024*1024))
	if err != nil {
		return err
	}
	if res.StatusCode != 200 {
		return fmt.Errorf("Qareen HTTP %d: %s", res.StatusCode, data)
	}
	if result != nil {
		return json.Unmarshal(data, result)
	}
	return nil
}
func proxyAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != "GET" && r.Method != "POST" {
		http.Error(w, "Method not allowed", 405)
		return
	}
	req, err := http.NewRequestWithContext(r.Context(), r.Method, ServerURL+r.URL.RequestURI(), http.MaxBytesReader(w, r.Body, 256000))
	if err != nil {
		http.Error(w, "Invalid request", 400)
		return
	}
	req.Header.Set("Authorization", r.Header.Get("Authorization"))
	req.Header.Set("Content-Type", r.Header.Get("Content-Type"))
	req.Header.Set("Origin", r.Header.Get("Origin"))
	req.ContentLength = r.ContentLength
	// Never silently add the local administrator token to an untrusted browser request.
	client := &http.Client{Timeout: 70 * time.Second, Transport: http.DefaultTransport}
	res, err := client.Do(req)
	if err != nil {
		http.Error(w, "Service unavailable", 502)
		return
	}
	defer res.Body.Close()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(res.StatusCode)
	io.Copy(w, io.LimitReader(res.Body, 16*1024*1024))
}
func serviceControl(action string) {
	command := exec.Command("systemctl", "--user", action, "qareen-embedding.service", "qareen.service")
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	if err := command.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "Install services with scripts/install.sh; for foreground use python embedding_server.py and qareen run-daemon.")
		os.Exit(1)
	}
}
func runDoctor() {
	for _, binary := range []string{"python3", "systemctl", "journalctl", "dbus-monitor", "hyprctl"} {
		_, err := exec.LookPath(binary)
		fmt.Printf("%-18s available=%v\n", binary, err == nil)
	}
	var health interface{}
	err := callAPI("GET", "/health", nil, &health)
	fmt.Printf("API ready: %v\n", err == nil)
	profile, err := findFirefoxProfile()
	fmt.Printf("Firefox profile: %s (%v)\n", profile, err)
	fmt.Printf("Private data: %s\n", qareenDataDir())
}
func randomID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}
func parseINI(text string) map[string]map[string]string {
	sections := map[string]map[string]string{}
	current := ""
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			current = strings.Trim(line, "[]")
			sections[current] = map[string]string{}
			continue
		}
		if current != "" {
			parts := strings.SplitN(line, "=", 2)
			if len(parts) == 2 {
				sections[current][strings.TrimSpace(parts[0])] = strings.TrimSpace(parts[1])
			}
		}
	}
	return sections
}
