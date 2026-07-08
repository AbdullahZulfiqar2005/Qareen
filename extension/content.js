console.log("Qareen Companion Extension Activated!");

// Keep track of recently sent/received messages to prevent double logging
const recentLogs = new Map(); // text -> timestamp
function isDuplicate(text) {
    const clean = text.trim().toLowerCase();
    if (!clean) return true;
    const now = Date.now();
    if (recentLogs.has(clean)) {
        const lastTime = recentLogs.get(clean);
        if (now - lastTime < 3000) { // 3 seconds threshold
            return true;
        }
    }
    recentLogs.set(clean, now);
    // Keep map size small
    if (recentLogs.size > 100) {
        const firstKey = recentLogs.keys().next().value;
        recentLogs.delete(firstKey);
    }
    return false;
}

// Log page browsing visit on load (excluding the specialized high-traffic domains)
const initialHost = window.location.hostname;
if (initialHost && 
    !initialHost.includes("chatgpt.com") && 
    !initialHost.includes("web.whatsapp.com") && 
    !initialHost.includes("meet.google.com")) {
    const title = document.title || initialHost;
    const url = window.location.href;
    sendWebLog("browse", initialHost, `Visited webpage: ${title} (URL: ${url})`, "You");
}

function getWhatsAppContact() {
    let name = "Unknown";
    const mainHeader = document.querySelector('#main header');
    if (mainHeader) {
        // Query elements with [title] and filter out utility titles
        const titleEls = mainHeader.querySelectorAll('[title]');
        for (const el of titleEls) {
            const titleVal = el.getAttribute('title');
            if (titleVal && titleVal.trim()) {
                const lower = titleVal.trim().toLowerCase();
                if (lower !== "profile details" && 
                    lower !== "search…" && 
                    lower !== "click here for contact info" && 
                    lower !== "menu") {
                    name = titleVal.trim();
                    break;
                }
            }
        }
        
        // Fallback: scan spans in main header for text content
        if (name === "Unknown") {
            const spans = mainHeader.querySelectorAll('span');
            for (const span of spans) {
                const text = span.innerText.trim();
                if (text && text.length > 0 && 
                    !/^(online|typing\.\.\.|click here for contact info|profile details|$)/i.test(text) && 
                    text.length < 50) {
                    name = text;
                    break;
                }
            }
        }
    }
    return name;
}

function sendWebLog(site, target, content, sender = "You") {
    content = content.trim();
    if (!content) return;
    if (isDuplicate(content)) return;
    
    console.log(`Qareen Content Script sending message: [${site}] -> ${content.substring(0, 30)}...`);
    try {
        chrome.runtime.sendMessage({
            action: "logWeb",
            site: site,
            target: target,
            content: content,
            sender: sender
        });
    } catch (err) {
        console.error("Qareen runtime send error:", err);
    }
}

// --- METHOD 1: KEYDOWN & CLICK LISTENERS (CAPTURE PHASE) ---

// Capture phase keydown listener
document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        let text = "";
        let site = "";
        let target = "";
        const host = window.location.hostname;

        // Specialized checkers
        if (host.includes("chatgpt.com")) {
            const textarea = document.getElementById("prompt-textarea");
            if (textarea && e.target === textarea) {
                text = textarea.value;
                site = "chatgpt";
                target = document.title.replace("ChatGPT - ", "").replace("ChatGPT", "").trim();
                if (!target) target = "New Chat";
                if (text) setTimeout(() => sendWebLog(site, target, text, "You"), 100);
            }
        } else if (host.includes("web.whatsapp.com")) {
            const input = document.querySelector('div[contenteditable="true"]');
            if (input && input.contains(e.target)) {
                text = input.innerText;
                site = "whatsapp";
                target = getWhatsAppContact();
                if (text) setTimeout(() => sendWebLog(site, target, text, "You"), 200);
            }
        } else if (!host.includes("meet.google.com")) {
            // General websites text logger
            const targetEl = e.target;
            if (!targetEl) return;

            const isInput = targetEl.tagName === 'INPUT' || targetEl.tagName === 'TEXTAREA';
            const isContentEditable = targetEl.getAttribute('contenteditable') === 'true' || targetEl.isContentEditable;

            if (isInput || isContentEditable) {
                // SENSITIVITY PRIVACY CHECK: Do not capture passwords or sensitive keys
                const type = (targetEl.getAttribute('type') || '').toLowerCase();
                const id = (targetEl.id || '').toLowerCase();
                const name = (targetEl.name || '').toLowerCase();
                const className = (targetEl.className || '').toLowerCase();

                const isSensitive = type === 'password' || 
                                    id.includes('pass') || name.includes('pass') || className.includes('pass') ||
                                    id.includes('card') || name.includes('card') || className.includes('card') ||
                                    id.includes('cvv') || name.includes('cvv') || className.includes('cvv') ||
                                    id.includes('ssn') || name.includes('ssn') || className.includes('ssn') ||
                                    id.includes('token') || name.includes('token') || className.includes('token') ||
                                    id.includes('key') || name.includes('key') || className.includes('key') ||
                                    id.includes('secret') || name.includes('secret') || className.includes('secret');

                if (isSensitive) return;

                text = isInput ? targetEl.value : targetEl.innerText;
                if (text && text.trim().length > 3) {
                    const pageTitle = document.title || host;
                    const fieldName = name || id || targetEl.tagName.toLowerCase();
                    const logText = `Submitted text in field '${fieldName}' on page '${pageTitle}': ${text.trim()}`;
                    setTimeout(() => sendWebLog("input", host, logText, "You"), 150);
                }
            }
        }
    }
}, true); // Capture phase is key!

// Capture phase click listener
document.addEventListener('click', (e) => {
    let text = "";
    let site = "";
    let target = "";
    const host = window.location.hostname;

    if (host.includes("chatgpt.com")) {
        const sendBtn = document.querySelector('[data-testid="send-button"]') || e.target.closest('[data-testid="send-button"]');
        if (sendBtn) {
            const textarea = document.getElementById("prompt-textarea");
            if (textarea) {
                text = textarea.value;
                site = "chatgpt";
                target = document.title.replace("ChatGPT - ", "").replace("ChatGPT", "").trim();
                if (!target) target = "New Chat";
            }
        }
    } else if (host.includes("web.whatsapp.com")) {
        const sendBtn = document.querySelector('button span[data-icon="send"]') || e.target.closest('button span[data-icon="send"]');
        if (sendBtn) {
            const input = document.querySelector('div[contenteditable="true"]');
            if (input) {
                text = input.innerText;
                site = "whatsapp";
                target = getWhatsAppContact();
            }
        }
    }

    if (text && site) {
        sendWebLog(site, target, text, "You");
    }
}, true); // Capture phase is key!

// --- METHOD 2: DOM MUTATION OBSERVER (FALLBACK & AUTOMATIC SCREEN LOGGER) ---

const observer = new MutationObserver((mutations) => {
    const host = window.location.hostname;
    
    // 1. Check Google Meet captions
    if (host.includes("meet.google.com")) {
        processMeetCaptions();
        return;
    }

    // 2. Only scan chat logs on chatgpt and whatsapp to prevent CPU lag on generic websites
    if (!host.includes("chatgpt.com") && !host.includes("web.whatsapp.com")) {
        return;
    }

    for (const mutation of mutations) {
        if (mutation.type === 'childList') {
            for (const node of mutation.addedNodes) {
                if (node.nodeType !== Node.ELEMENT_NODE) continue;
                
                // WhatsApp check
                if (host.includes("web.whatsapp.com")) {
                    const msgDiv = node.classList.contains('copyable-text') ? node : node.querySelector('.copyable-text');
                    if (msgDiv && msgDiv.hasAttribute('data-pre-plain-text')) {
                        const meta = msgDiv.getAttribute('data-pre-plain-text');
                        const match = meta.match(/\]\s*(.*?):\s*$/);
                        if (match) {
                            let sender = match[1].trim();
                            
                            // Check if this is an outgoing message (sent by you)
                            const lowerSender = sender.toLowerCase();
                            const isOutgoing = msgDiv.closest('.message-out') !== null ||
                                               lowerSender === "you" ||
                                               lowerSender.includes("abdullah") ||
                                               lowerSender.includes("zulfiqar");
                            if (isOutgoing) {
                                sender = "You";
                            }
                            
                            const textSpan = msgDiv.querySelector('span.selectable-text.copyable-text');
                            if (textSpan) {
                                const text = textSpan.innerText;
                                const chatName = getWhatsAppContact();
                                sendWebLog("whatsapp", chatName, text, sender);
                                continue;
                            }
                        }
                    }
                    
                    // Fallback to class checks
                    const msgOut = node.classList.contains('message-out') ? node : node.querySelector('.message-out');
                    if (msgOut && !msgOut.querySelector('.copyable-text')) {
                        const textSpan = msgOut.querySelector('span.selectable-text.copyable-text');
                        if (textSpan) {
                            const text = textSpan.innerText;
                            const chatName = getWhatsAppContact();
                            sendWebLog("whatsapp", chatName, text, "You");
                        }
                    }
                    const msgIn = node.classList.contains('message-in') ? node : node.querySelector('.message-in');
                    if (msgIn && !msgIn.querySelector('.copyable-text')) {
                        const textSpan = msgIn.querySelector('span.selectable-text.copyable-text');
                        if (textSpan) {
                            const text = textSpan.innerText;
                            const chatName = getWhatsAppContact();
                            sendWebLog("whatsapp", chatName, text, chatName);
                        }
                    }
                }
                
                // ChatGPT check: user turn
                if (host.includes("chatgpt.com")) {
                    const turn = node.querySelector('[data-testid^="conversation-turn-"]') || 
                                 (node.getAttribute && node.getAttribute('data-testid') && node.getAttribute('data-testid').startsWith('conversation-turn-') ? node : null);
                    if (turn) {
                        const isUser = turn.querySelector('[data-message-author-role="user"]');
                        if (isUser) {
                            const textDiv = turn.querySelector('.whitespace-pre-wrap');
                            if (textDiv) {
                                const text = textDiv.innerText;
                                let target = document.title.replace("ChatGPT - ", "").replace("ChatGPT", "").trim();
                                if (!target) target = "New Chat";
                                sendWebLog("chatgpt", target, text, "You");
                            }
                        }
                    }
                }
            }
        }
    }
});

// Start observer
observer.observe(document.body, { childList: true, subtree: true });

// --- GOOGLE MEET CLOSED CAPTIONS PROCESSOR ---

const activeCaptions = new Map();
let captionIdCounter = 0;

function processMeetCaptions() {
    const container = document.querySelector('div[jsname="x37oKu"]') || 
                      document.querySelector('[aria-label="Captions"]') ||
                      document.querySelector('.bh44bd');
    if (!container) return;
    
    const blocks = container.children;
    const currentActiveElements = new Set();
    
    for (const block of blocks) {
        currentActiveElements.add(block);
        
        if (!block.dataset.qareenId) {
            block.dataset.qareenId = "cap_" + (++captionIdCounter);
            activeCaptions.set(block.dataset.qareenId, {
                speaker: "Unknown",
                text: "",
                lastLoggedText: ""
            });
        }
        
        const state = activeCaptions.get(block.dataset.qareenId);
        
        // Resolve speaker name
        let speaker = "Speaker";
        const nameEl = block.querySelector('.zs7s8d') || 
                       block.querySelector('.jmjoTe') || 
                       block.firstElementChild;
        if (nameEl && nameEl.innerText.trim()) {
            speaker = nameEl.innerText.trim();
        }
        state.speaker = speaker;
        
        // Resolve spoken text
        let speechText = "";
        const textEl = block.querySelector('.iTTPOb') || 
                       block.querySelector('.jTuls') || 
                       block.querySelector('span') || 
                       block.lastElementChild;
        if (textEl) {
            speechText = textEl.innerText.trim();
        }
        
        if (speechText && speechText !== state.text) {
            state.text = speechText;
            
            const newWords = speechText.slice(state.lastLoggedText.length).trim();
            const sentenceEndRegex = /[.!?\u060C\u061F]\s*$/;
            if (sentenceEndRegex.test(newWords) || newWords.length > 50) {
                sendWebLog("google-meet", "Google Meet", newWords, speaker);
                state.lastLoggedText = speechText;
            }
        }
    }
    
    // Cleanup old captions and dump remaining unlogged text
    for (const key of activeCaptions.keys()) {
        let found = false;
        for (const block of currentActiveElements) {
            if (block.dataset.qareenId === key) {
                found = true;
                break;
            }
        }
        if (!found) {
            const state = activeCaptions.get(key);
            const remainingText = state.text.slice(state.lastLoggedText.length).trim();
            if (remainingText.length > 0) {
                sendWebLog("google-meet", "Google Meet", remainingText, state.speaker);
            }
            activeCaptions.delete(key);
        }
    }
}
