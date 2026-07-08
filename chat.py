import os
import sys
import subprocess
from langchain_community.vectorstores import FAISS
from langchain_groq import ChatGroq
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import AIMessage, ToolMessage
from qareen_embeddings import RemoteEmbeddings
from qareen_dotenv import load_dotenv

load_dotenv()

if "GROQ_API_KEY" not in os.environ:
    print(
        "Error: GROQ_API_KEY environment variable is not set.\n"
        "Export it before running Arch-Sage, e.g.:\n"
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
print(f"      🧙‍♂️ ARCH-SAGE: AGENTIC SYSTEM TROUBLESHOOTER")
print(f"=================================================={RESET}")

print(f"{GREEN}[System] Connecting to local embedding server and loading FAISS vector index...{RESET}")
embedding_model = RemoteEmbeddings()
vector_store = FAISS.load_local("arch_wiki_index", embedding_model, allow_dangerous_deserialization=True)
retriever = vector_store.as_retriever(search_kwargs={"k": 3})
print(f"{GREEN}[System] Search index loaded! Ready to assist.{RESET}")

@tool
def arch_wiki_search(query: str) -> str:
    """Searches the local Arch Wiki FAISS index for documentation, troubleshooting guides, and configurations.
    Use this to look up technical documentation or solutions for Arch Linux."""
    docs = retriever.invoke(query)
    results = []
    for doc in docs:
        source = doc.metadata.get('source', 'Unknown')
        results.append(f"--- Source: {source} ---\n{doc.page_content}")
    return "\n\n".join(results)

@tool
def read_system_file(file_path: str, start_line: int = 1, num_lines: int = 50) -> str:
    """Reads lines from a file on the local system.
    Use this to inspect system configuration files (e.g., ~/.config/hypr/hyprland.conf, /etc/fstab) or local logs."""
    expanded_path = os.path.expanduser(file_path)
    if not os.path.exists(expanded_path):
        return f"Error: File {file_path} does not exist."
    if os.path.isdir(expanded_path):
        return f"Error: {file_path} is a directory. Choose a specific file."
    try:
        with open(expanded_path, 'r', errors='replace') as f:
            lines = f.readlines()
        total_lines = len(lines)
        start_idx = max(0, start_line - 1)
        end_idx = min(total_lines, start_idx + num_lines)
        selected_lines = lines[start_idx:end_idx]
        content = "".join(selected_lines)
        return f"Showing lines {start_idx + 1}-{end_idx} of {total_lines} in {file_path}:\n\n{content}"
    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"

@tool
def run_diagnostic(command: str) -> str:
    """Runs a read-only diagnostic command on the system (e.g., 'systemctl status', 'ip a', 'pgrep', 'ls').
    Use this to inspect system state, processes, or configurations.
    Do NOT use this for writing files, restarting services, or applying modifications."""
    print(f"\n⚡ {YELLOW}[Diagnostic Run]{RESET} {command}")
    try:
        res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=10)
        output = f"Exit code: {res.returncode}\n"
        if res.stdout:
            output += f"Stdout:\n{res.stdout}\n"
        if res.stderr:
            output += f"Stderr:\n{res.stderr}\n"
        return output
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 10 seconds."
    except Exception as e:
        return f"Error executing command: {str(e)}"

@tool
def apply_fix(command: str) -> str:
    """Executes a command that modifies system state (e.g., writes files, restarts services, installs packages).
    Always prompts the user for manual confirmation before execution.
    Use this tool to apply corrective changes after diagnostics suggest a clear fix."""
    print(f"\n⚠️  {RED}[SAFETY CONFIRMATION REQUIRED]{RESET}")
    print(f"   The agent wants to execute the following command to apply a fix:")
    print(f"   👉 {YELLOW}{BOLD}{command}{RESET}")
    try:
        confirm = input("   Approve execution? (y/N): ").strip().lower()
    except EOFError:
        confirm = 'n'
        
    if confirm == 'y':
        print(f"⚡ {GREEN}[Executing Fix]{RESET}...")
        try:
            res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=15)
            output = f"Exit code: {res.returncode}\n"
            if res.stdout:
                output += f"Stdout:\n{res.stdout}\n"
            if res.stderr:
                output += f"Stderr:\n{res.stderr}\n"
            return output
        except subprocess.TimeoutExpired:
            return "Error: Command timed out after 15 seconds."
        except Exception as e:
            return f"Error executing command: {str(e)}"
    else:
        print(f"❌ {RED}[Execution Cancelled by User]{RESET}")
        return "Error: Command was rejected by the user. Please formulate an alternative solution."

# Setup LLM and tools
llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
tools = [arch_wiki_search, read_system_file, run_diagnostic, apply_fix]

system_prompt = (
    "You are Arch-Sage, an advanced agentic system troubleshooting assistant for Arch Linux.\n"
    "Your job is to diagnose and solve system configuration issues using your tools:\n"
    "1. arch_wiki_search: Query the Arch Wiki FAISS index for relevant documentation and common solutions.\n"
    "2. read_system_file: Inspect local configuration files or log files.\n"
    "3. run_diagnostic: Check the system state (e.g., service status, processes, file lists).\n"
    "4. apply_fix: Run commands to make changes (e.g., config changes, restart services). This prompts the user for confirmation.\n\n"
    "Troubleshooting Strategy (Hit-and-Trial):\n"
    "- When user reports an issue, check the wiki and run diagnostic checks to find the discrepancy.\n"
    "- Make hypotheses and propose fixes. Execute fixes using apply_fix.\n"
    "- Crucial: After applying a fix, run diagnostic checks to verify if the issue is solved.\n"
    "- If a fix doesn't work, don't give up. Learn from the tool outputs, refine your understanding, try alternative fixes (hit-and-trial), and check again.\n"
    "- Keep the user updated on your reasoning at each step.\n"
    "- Provide a clear summary once the issue is solved or if it's determined to be unresolvable."
)

agent_executor = create_react_agent(llm, tools, prompt=system_prompt)

print(f"\n{GREEN}--- Arch-Sage Active! Type 'exit' or 'quit' to close. ---{RESET}")
while True:
    try:
        query = input(f"\n{BOLD}User:{RESET} ")
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")
        break
        
    if query.strip().lower() in ['exit', 'quit']:
        print("Goodbye!")
        break
        
    if not query.strip():
        continue
        
    print(f"\n{BLUE}{BOLD}🕵️‍♂️ Starting agent reasoning & troubleshooting flow...{RESET}")
    
    try:
        for event in agent_executor.stream({"messages": [("human", query)]}):
            if 'agent' in event:
                messages = event['agent']['messages']
                for msg in messages:
                    if msg.content:
                        print(f"\n🧠 {BLUE}[Arch-Sage Thought]{RESET}\n{msg.content}")
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            print(f"\n🔧 {YELLOW}[Tool Call]{RESET} Calling '{tc['name']}' with arguments:")
                            for k, v in tc['args'].items():
                                print(f"   🔹 {k}: {v}")
            elif 'tools' in event:
                messages = event['tools']['messages']
                for msg in messages:
                    content_str = msg.content
                    if len(content_str) > 1000:
                        content_str = content_str[:1000] + "\n... (truncated for readability)"
                    print(f"\n📦 {GREEN}[Tool Result]{RESET} from '{msg.name}':\n{content_str}")
    except Exception as e:
        print(f"\n❌ {RED}[Agent Execution Error]{RESET} {str(e)}")
