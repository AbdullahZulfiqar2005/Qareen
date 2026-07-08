import os
import sys
import time
import argparse
from langchain_community.vectorstores import FAISS
from langchain_groq import ChatGroq
from qareen_embeddings import RemoteEmbeddings
from qareen_dotenv import load_dotenv

load_dotenv()

if "GROQ_API_KEY" not in os.environ:
    print(
        "Error: GROQ_API_KEY environment variable is not set.\n"
        "Export it before running this tool, e.g.:\n"
        '  export GROQ_API_KEY="your-key-here"\n'
        "Get a key at https://console.groq.com/keys"
    )
    sys.exit(1)

# Color constants
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Print banner
print(f"{BLUE}{BOLD}==================================================")
print(f"      ❄️  HYPRLAND DIAGNOSTIC & MONITORING AGENT")
print(f"=================================================={RESET}")

_retriever_cache = None


def load_retriever():
    """Load the FAISS vector index of Arch Wiki, embedding queries via the
    already-running local embedding server instead of loading a third copy
    of the transformer model into this process. Cached process-wide since
    --monitor can trigger analysis many times in one long-running session and
    reloading the index from disk each time is wasted I/O and CPU."""
    global _retriever_cache
    if _retriever_cache is None:
        embedding_model = RemoteEmbeddings()
        vector_store = FAISS.load_local("arch_wiki_index", embedding_model, allow_dangerous_deserialization=True)
        _retriever_cache = vector_store.as_retriever(search_kwargs={"k": 3})
    return _retriever_cache

def get_hyprland_log():
    """Locate the most recent active Hyprland log or crash report."""
    # 1. Check crash reports in ~/.cache/hyprland/
    cache_dir = os.path.expanduser("~/.cache/hyprland")
    if os.path.exists(cache_dir):
        crashes = [os.path.join(cache_dir, f) for f in os.listdir(cache_dir) if f.startswith("hyprlandCrashReport")]
        if crashes:
            latest_crash = max(crashes, key=os.path.getmtime)
            # If it's a recent crash report, let's use it
            return latest_crash, "crash_report"

    # 2. Check XDG_RUNTIME_DIR
    uid = os.getuid()
    xdg = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{uid}')
    hypr_dir = os.path.join(xdg, 'hypr')
    if os.path.exists(hypr_dir):
        subdirs = [os.path.join(hypr_dir, d) for d in os.listdir(hypr_dir)]
        subdirs = [d for d in subdirs if os.path.isdir(d)]
        if subdirs:
            latest_subdir = max(subdirs, key=os.path.getmtime)
            log_path = os.path.join(latest_subdir, 'hyprland.log')
            if os.path.exists(log_path):
                return log_path, "active_log"
                
    # 3. Check ~/.local/share/hyprland/ (alternative log directory)
    alt_dir = os.path.expanduser("~/.local/share/hyprland")
    alt_log = os.path.join(alt_dir, "hyprland.log")
    if os.path.exists(alt_log):
        return alt_log, "active_log"
        
    return None, None

def analyze_failure(log_path, log_type):
    """Scan and analyze log file for critical errors, showing diagnosis and solution."""
    print(f"\n{BLUE}[System] Analyzing logs in: {log_path} ({log_type}){RESET}")
    
    try:
        with open(log_path, 'r', errors='replace') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"{RED}Error reading file: {str(e)}{RESET}")
        return

    critical_context = ""
    error_line = ""
    
    if log_type == "crash_report":
        # Crash reports are usually small, grab the final parts
        critical_context = "".join(lines[-100:])
        error_line = lines[0] if lines else "Hyprland crashed"
    else:
        # active_log can be large, scan the last 2000 lines
        search_range = lines[-2000:]
        found_err = False
        err_idx = -1
        
        keywords = ["[CRITICAL]", "[ERROR]", "SIGSEGV", "Segmentation fault", "Backend failed to start", "panic", "failed to open DRM device"]
        
        for idx, line in enumerate(search_range):
            if any(kw in line for kw in keywords):
                found_err = True
                err_idx = idx
                error_line = line
                
        if found_err:
            # Capture surrounding log lines
            start = max(0, err_idx - 15)
            end = min(len(search_range), err_idx + 35)
            critical_context = "".join(search_range[start:end])
        else:
            # If no critical errors found, analyze the end of the log
            critical_context = "".join(lines[-60:])
            error_line = lines[-1] if lines else "Hyprland terminated"
            
    print(f"\n{YELLOW}[Recent Log Snippet]{RESET}")
    print("-" * 60)
    # Output the last 15 lines of the critical context to screen
    context_lines = [l.strip() for l in critical_context.split('\n') if l.strip()]
    for l in context_lines[-15:]:
        print(l)
    print("-" * 60)

    # 3. Retrieve documentation
    print(f"\n{GREEN}[System] Loading index and searching Arch Wiki...{RESET}")
    retriever = load_retriever()
    
    clean_query = error_line
    for tag in ["[CRITICAL]", "[ERROR]", "DEBUG", "WARN"]:
        clean_query = clean_query.replace(tag, "")
    
    docs = retriever.invoke(clean_query[:120])
    wiki_context = "\n\n".join([f"Source: {d.metadata.get('source', 'Wiki')}\n{d.page_content}" for d in docs])
    
    # 4. Generate diagnosis and fixes
    print(f"{GREEN}[System] Requesting diagnostic advice from AI...{RESET}")
    llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
    
    prompt = f"""You are an expert system administrator diagnosing a Hyprland compositor failure on Arch Linux.
    Below is the relevant log context/error message and excerpts from the Arch Wiki.
    
    --- Log Context ---
    {critical_context}
    
    --- Arch Wiki Excerpts ---
    {wiki_context}
    
    Please provide:
    1. DIAGNOSIS: Explain clearly what went wrong, what the log errors mean, and the root cause.
    2. SOLUTION: Provide step-by-step instructions on how to fix this issue.
    3. COMMANDS: List the exact terminal commands the user needs to execute.
    """
    
    response = llm.invoke(prompt)
    
    print(f"\n{BLUE}{BOLD}==================================================")
    print("                DIAGNOSTIC REPORT")
    print(f"=================================================={RESET}")
    print(response.content)

def trigger_live_analysis(critical_context, error_line):
    """Run diagnostic advice on real-time captured failure."""
    print(f"\n{GREEN}[System] Fetching solution details for the error...{RESET}")
    retriever = load_retriever()
    
    clean_query = error_line
    for tag in ["[CRITICAL]", "[ERROR]", "DEBUG", "WARN"]:
        clean_query = clean_query.replace(tag, "")
        
    docs = retriever.invoke(clean_query[:120])
    wiki_context = "\n\n".join([f"Source: {d.metadata.get('source', 'Wiki')}\n{d.page_content}" for d in docs])
    
    llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
    prompt = f"""You are an expert system administrator diagnosing a Hyprland compositor failure on Arch Linux.
    Below is the relevant log context/error message and excerpts from the Arch Wiki.
    
    --- Log Context ---
    {critical_context}
    
    --- Arch Wiki Excerpts ---
    {wiki_context}
    
    Please provide:
    1. DIAGNOSIS: Explain clearly what went wrong, what the log errors mean, and the root cause.
    2. SOLUTION: Provide step-by-step instructions on how to fix this issue.
    3. COMMANDS: List the exact terminal commands the user needs to execute.
    """
    response = llm.invoke(prompt)
    print(f"\n{BLUE}{BOLD}==================================================")
    print("             LIVE DIAGNOSTIC REPORT")
    print(f"=================================================={RESET}")
    print(response.content)

def monitor_log(log_path):
    """Monitor log file in real time (tail -f)."""
    print(f"{GREEN}[System] Starting live monitor on {log_path}...{RESET}")
    print(f"{GREEN}[System] Watching for critical failures... (Press Ctrl+C to stop){RESET}")
    
    try:
        with open(log_path, 'r', errors='replace') as f:
            # Go to the end of the file
            f.seek(0, os.SEEK_END)
            
            buffer = []
            capturing = False
            lines_to_capture = 0
            
            keywords = ["[CRITICAL]", "[ERROR]", "SIGSEGV", "Segmentation fault", "Backend failed to start", "panic"]
            
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                
                # Check for critical errors
                if any(kw in line for kw in keywords):
                    print(f"\n🚨 {RED}{BOLD}[CRITICAL ALERT DETECTED]{RESET} {line.strip()}")
                    capturing = True
                    lines_to_capture = 25
                    buffer = [line]
                elif capturing:
                    buffer.append(line)
                    lines_to_capture -= 1
                    if lines_to_capture <= 0:
                        capturing = False
                        context_str = "".join(buffer)
                        trigger_live_analysis(context_str, buffer[0])
                        print(f"\n{GREEN}[System] Resuming monitor on {log_path}...{RESET}")
    except KeyboardInterrupt:
        print(f"\n{YELLOW}[System] Monitor stopped by user.{RESET}")

def main():
    parser = argparse.ArgumentParser(description="Arch Hyprland Log Monitor & Diagnostic AI")
    parser.add_argument("--analyze", action="store_true", help="Analyze existing log file (default)")
    parser.add_argument("--monitor", action="store_true", help="Monitor the log file in real-time")
    args = parser.parse_args()

    log_path, log_type = get_hyprland_log()
    if not log_path:
        print(f"{RED}Error: Could not locate active Hyprland log or crash reports on this system.{RESET}")
        sys.exit(1)
        
    if args.monitor:
        monitor_log(log_path)
    else:
        # Default is analyze
        analyze_failure(log_path, log_type)

if __name__ == "__main__":
    main()
