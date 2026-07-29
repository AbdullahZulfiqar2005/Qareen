console.log("Qareen Companion Extension Activated!");

// Keep track of recently sent/received messages to prevent double logging
const recentLogs = new Map(); // text -> timestamp

// Self-learning user identity for WhatsApp
let detectedUserRealName = "";
let lastTypedText = "";
let lastTypedTime = 0;
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

function sendWebLog(site, target, content, sender = "You") {
    content = content.trim();
    if (!content) return;
    if (isDuplicate(content)) return;
    
    console.log(`Qareen Content Script sending log to server: [${site}] -> ${content.substring(0, 50)}...`);
    try {
        fetch("http://127.0.0.1:2846/log-web", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                site: site,
                target: target,
                content: content,
                sender: sender
            })
        })
        .catch(err => {
            console.error("Qareen local server logging failed:", err);
        });
    } catch (err) {
        console.error("Qareen fetch error:", err);
    }
}

// Check for input sensitivity
function isSensitiveInput(targetEl) {
    if (!targetEl) return false;
    const type = (targetEl.getAttribute('type') || '').toLowerCase();
    const id = (targetEl.id || '').toLowerCase();
    const name = (targetEl.name || '').toLowerCase();
    const className = (targetEl.className || '').toLowerCase();

    return type === 'password' || 
           id.includes('pass') || name.includes('pass') || className.includes('pass') ||
           id.includes('card') || name.includes('card') || className.includes('card') ||
           id.includes('cvv') || name.includes('cvv') || className.includes('cvv') ||
           id.includes('ssn') || name.includes('ssn') || className.includes('ssn') ||
           id.includes('token') || name.includes('token') || className.includes('token') ||
           id.includes('key') || name.includes('key') || className.includes('key') ||
           id.includes('secret') || name.includes('secret') || className.includes('secret');
}

// Listen to generic keydowns & clicks for chat inputs
document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        const host = window.location.hostname;
        const targetEl = e.target;
        if (!targetEl || isSensitiveInput(targetEl)) return;

        let text = "";
        let site = "";
        let target = "";

        if (host.includes("chatgpt.com")) {
            const textarea = document.getElementById("prompt-textarea");
            if (textarea && targetEl === textarea) {
                text = textarea.value || textarea.innerText || "";
                site = "chatgpt";
                target = document.title.replace("ChatGPT - ", "").replace("ChatGPT", "").trim() || "New Chat";
                if (text) setTimeout(() => sendWebLog(site, target, text, "You"), 100);
            }
        } else if (host.includes("web.whatsapp.com")) {
            const input = getWhatsAppInput();
            if (input && input.contains(targetEl)) {
                text = input.innerText;
                site = "whatsapp";
                target = getWhatsAppContact();
                if (text) {
                    lastTypedText = text.trim();
                    lastTypedTime = Date.now();
                    setTimeout(() => sendWebLog(site, target, text, "You"), 200);
                }
            }
        } else if (host.includes("gemini.google.com")) {
            const editor = document.querySelector('div[contenteditable="true"]') || document.querySelector('.input-area textarea');
            if (editor && (targetEl === editor || editor.contains(targetEl))) {
                text = editor.innerText || editor.value;
                site = "gemini";
                target = "Gemini Chat";
                if (text) setTimeout(() => sendWebLog(site, target, text, "You"), 150);
            }
        } else if (host.includes("claude.ai")) {
            const editor = document.querySelector('div[contenteditable="true"]') || document.querySelector('[role="textbox"]');
            if (editor && (targetEl === editor || editor.contains(targetEl))) {
                text = editor.innerText;
                site = "claude";
                target = document.title.replace("Claude", "").trim() || "Claude Chat";
                if (text) setTimeout(() => sendWebLog(site, target, text, "You"), 150);
            }
        } else if (host.includes("x.com") || host.includes("grok.com")) {
            if (window.location.pathname.includes("/grok") || host.includes("grok.com")) {
                const editor = document.querySelector('div[contenteditable="true"]') || document.querySelector('textarea');
                if (editor && (targetEl === editor || editor.contains(targetEl))) {
                    text = editor.innerText || editor.value;
                    site = "grok";
                    target = "Grok Chat";
                    if (text) setTimeout(() => sendWebLog(site, target, text, "You"), 150);
                }
            }
        } else if (host.includes("console.groq.com")) {
            const editor = document.querySelector('textarea') || document.querySelector('div[contenteditable="true"]');
            if (editor && (targetEl === editor || editor.contains(targetEl))) {
                text = editor.value || editor.innerText;
                site = "groq";
                target = "Groq Playground";
                if (text) setTimeout(() => sendWebLog(site, target, text, "You"), 150);
            }
        } else if (host.includes("linkedin.com")) {
            const editor = document.querySelector('.msg-form__contenteditable') || document.querySelector('div[role="textbox"]');
            if (editor && (targetEl === editor || editor.contains(targetEl))) {
                text = editor.innerText;
                site = "linkedin";
                target = getLinkedInContact();
                if (text) setTimeout(() => sendWebLog(site, target, text, "You"), 200);
            }
        } else if (host.includes("instagram.com")) {
            const editor = document.querySelector('div[role="textbox"]') || document.querySelector('textarea');
            if (editor && (targetEl === editor || editor.contains(targetEl))) {
                text = editor.innerText || editor.value;
                site = "instagram";
                target = getInstagramContact();
                if (text) setTimeout(() => sendWebLog(site, target, text, "You"), 200);
            }
        } else if (host.includes("chess.com")) {
            const chatInput = document.querySelector('.chat-input-input') || document.querySelector('input[placeholder*="chat" i]');
            if (chatInput && targetEl === chatInput) {
                text = chatInput.value;
                site = "chess";
                target = "Game Chat";
                if (text) setTimeout(() => sendWebLog(site, target, text, "You"), 100);
            }
        } else if (host.includes("reddit.com")) {
            const commentBox = document.querySelector('shreddit-composer textarea') || document.querySelector('textarea[placeholder*="comment"]');
            if (commentBox && targetEl === commentBox) {
                text = commentBox.value;
                site = "reddit";
                target = "Reddit Comment";
                if (text) setTimeout(() => sendWebLog(site, target, "Posted comment: " + text, "You"), 200);
            }
        } else if (host.includes("github.com")) {
            const commentBox = document.getElementById("new_comment_field") || document.querySelector('.comment-form-textarea');
            if (commentBox && targetEl === commentBox) {
                text = commentBox.value;
                site = "github";
                target = "GitHub Comment";
                if (text) setTimeout(() => sendWebLog(site, target, "Posted comment on GitHub: " + text, "You"), 200);
            }
        }
    }
}, true);

document.addEventListener('click', (e) => {
    const host = window.location.hostname;
    let text = "";
    let site = "";
    let target = "";

    if (host.includes("chatgpt.com")) {
        const sendBtn = document.querySelector('[data-testid="send-button"]') || e.target.closest('[data-testid="send-button"]');
        if (sendBtn) {
            const textarea = document.getElementById("prompt-textarea");
            if (textarea) {
                text = textarea.value || textarea.innerText || "";
                site = "chatgpt";
                target = document.title.replace("ChatGPT - ", "").replace("ChatGPT", "").trim() || "New Chat";
            }
        }
    } else if (host.includes("web.whatsapp.com")) {
        const sendBtn = document.querySelector('button span[data-icon="send"]') || e.target.closest('button span[data-icon="send"]');
        const closestButton = e.target.closest('button');
        const isSendClick = sendBtn || (closestButton && closestButton.querySelector('span[data-icon="send"]'));
        if (isSendClick) {
            const input = getWhatsAppInput();
            if (input) {
                text = input.innerText;
                site = "whatsapp";
                target = getWhatsAppContact();
                if (text) {
                    lastTypedText = text.trim();
                    lastTypedTime = Date.now();
                }
            }
        }
    } else if (host.includes("gemini.google.com")) {
        const sendBtn = document.querySelector('button.send-button') || document.querySelector('button[aria-label*="Send"]');
        if (sendBtn && sendBtn.contains(e.target)) {
            const editor = document.querySelector('div[contenteditable="true"]') || document.querySelector('.input-area textarea');
            if (editor) {
                text = editor.innerText || editor.value;
                site = "gemini";
                target = "Gemini Chat";
            }
        }
    } else if (host.includes("claude.ai")) {
        const sendBtn = document.querySelector('button[aria-label*="Send Message"]') || document.querySelector('button[data-testid="send-button"]');
        if (sendBtn && sendBtn.contains(e.target)) {
            const editor = document.querySelector('div[contenteditable="true"]') || document.querySelector('[role="textbox"]');
            if (editor) {
                text = editor.innerText;
                site = "claude";
                target = document.title.replace("Claude", "").trim() || "Claude Chat";
            }
        }
    } else if (host.includes("x.com") || host.includes("grok.com")) {
        if (window.location.pathname.includes("/grok") || host.includes("grok.com")) {
            const sendBtn = document.querySelector('div[data-testid="grok-send"]') || document.querySelector('button[aria-label*="Send"]');
            if (sendBtn && sendBtn.contains(e.target)) {
                const editor = document.querySelector('div[contenteditable="true"]') || document.querySelector('textarea');
                if (editor) {
                    text = editor.innerText || editor.value;
                    site = "grok";
                    target = "Grok Chat";
                }
            }
        }
    } else if (host.includes("console.groq.com")) {
        const sendBtn = document.querySelector('button[type="submit"]') || e.target.closest('button');
        if (sendBtn && sendBtn.contains(e.target)) {
            const editor = document.querySelector('textarea') || document.querySelector('div[contenteditable="true"]');
            if (editor) {
                text = editor.value || editor.innerText;
                site = "groq";
                target = "Groq Playground";
            }
        }
    } else if (host.includes("mail.google.com")) {
        // Intercept Sent Emails in Gmail
        const sendBtn = e.target.closest('div[role="button"][data-tooltip*="Send"]') || 
                          e.target.closest('div.T-I.aoO') || 
                          e.target.closest('.T-I-atl');
        if (sendBtn) {
            const composeBox = sendBtn.closest('div.M9') || sendBtn.closest('div[role="dialog"]');
            if (composeBox) {
                const toInput = composeBox.querySelector('.vN span[email]') || composeBox.querySelector('input[name="to"]');
                const toVal = toInput ? (toInput.getAttribute('email') || toInput.innerText || toInput.value) : "Unknown Recipient";
                const subjectInput = composeBox.querySelector('input[name="subjectbox"]');
                const subjectVal = subjectInput ? subjectInput.value : "No Subject";
                const bodyInput = composeBox.querySelector('div[role="textbox"]');
                const bodyVal = bodyInput ? bodyInput.innerText : "";
                
                if (bodyVal) {
                    sendWebLog("gmail", toVal, `Sent Email (Subject: ${subjectVal}): ${bodyVal}`, "You");
                }
            }
        }
    } else if (host.includes("linkedin.com")) {
        const sendBtn = e.target.closest('.msg-form__send-button') || e.target.closest('button[type="submit"]');
        if (sendBtn) {
            const editor = document.querySelector('.msg-form__contenteditable') || document.querySelector('div[role="textbox"]');
            if (editor) {
                text = editor.innerText;
                site = "linkedin";
                target = getLinkedInContact();
            }
        }
    } else if (host.includes("instagram.com")) {
        const sendBtn = e.target.closest('button[type="button"]') || e.target.closest('div[role="button"]');
        // Simple logic: if it's in the DM context and sendBtn looks like a text trigger button
        if (sendBtn && window.location.pathname.includes("/direct/")) {
            const editor = document.querySelector('div[role="textbox"]') || document.querySelector('textarea');
            if (editor) {
                // Wait briefly for text to send, or capture
                text = editor.innerText || editor.value;
                site = "instagram";
                target = getInstagramContact();
            }
        }
    }

    if (text && site) {
        sendWebLog(site, target, text, "You");
    }
}, true);

// Utility functions to resolve names
function getWhatsAppInput() {
    return document.querySelector('#main footer div[contenteditable="true"]') || 
           document.querySelector('footer div[contenteditable="true"]') || 
           document.querySelector('div[contenteditable="true"][data-tab="10"]');
}

function getWhatsAppContact() {
    // 1. Check main conversation header (using standard selectors)
    const mainHeader = document.querySelector('#main header') || 
                       document.querySelector('[data-testid="conversation-header"]') || 
                       document.querySelector('header');
    if (mainHeader) {
        const titleEls = mainHeader.querySelectorAll('[title]');
        for (const el of titleEls) {
            const titleVal = el.getAttribute('title');
            if (titleVal && titleVal.trim()) {
                const lower = titleVal.trim().toLowerCase();
                const ignores = ["profile details", "search…", "click here for contact info", "click here for group info", "menu", "mute notifications", "close chat", "voice call", "video call"];
                if (!ignores.includes(lower)) {
                    return titleVal.trim();
                }
            }
        }
        // Fallback to name text container inside header
        const nameEl = mainHeader.querySelector('span[dir="auto"]') || mainHeader.querySelector('.selectable-text');
        if (nameEl && nameEl.innerText && nameEl.innerText.trim()) {
            return nameEl.innerText.trim();
        }
    }
    
    // 2. Fallback to active list item in sidebar
    const activeItem = document.querySelector('[aria-selected="true"]') || 
                       document.querySelector('[data-tab="2"] div[class*="selected"]');
    if (activeItem) {
        const titleEl = activeItem.querySelector('[title]') || activeItem.querySelector('span[dir="auto"]');
        if (titleEl) {
            const val = titleEl.getAttribute('title') || titleEl.innerText;
            if (val && val.trim()) return val.trim();
        }
    }
    
    return "Unknown";
}

function checkIsGroupChat() {
    const subtitleEl = document.querySelector('#main header span[title]') || 
                       document.querySelector('[data-testid="conversation-header"] span[title]') ||
                       document.querySelector('header span[title]');
    if (subtitleEl) {
        const text = subtitleEl.getAttribute('title') || subtitleEl.innerText || "";
        if (text.includes(',') || text.toLowerCase().includes('group') || text.toLowerCase().includes('click here')) {
            if (text.toLowerCase().includes('contact info')) {
                return false;
            }
            return true;
        }
    }
    return false;
}

function checkIsOutgoing(msgDiv, sender) {
    if (sender && sender.toLowerCase() === "you") return true;
    if (detectedUserRealName && sender === detectedUserRealName) return true;
    
    // Self-learning heuristic:
    // If the bubble text matches what the user typed in the last 4 seconds
    const textSpan = msgDiv.querySelector('span.selectable-text.copyable-text') || 
                     msgDiv.querySelector('span.selectable-text') || 
                     msgDiv.querySelector('.selectable-text') ||
                     msgDiv.querySelector('span.copyable-text');
    if (textSpan && lastTypedText && (Date.now() - lastTypedTime < 4000)) {
        const bubbleText = textSpan.innerText.trim();
        if (bubbleText === lastTypedText) {
            if (sender && sender !== "You") {
                detectedUserRealName = sender;
                console.log(`[Qareen Heuristic] Dynamically learned user real name: ${detectedUserRealName}`);
            }
            return true;
        }
    }
    
    // Check 1: data-id prefix
    const msgContainer = msgDiv.closest('[data-id]') || msgDiv.querySelector('[data-id]');
    if (msgContainer) {
        const dataId = msgContainer.getAttribute('data-id');
        if (dataId) {
            if (dataId.startsWith('true_')) return true;
            if (dataId.startsWith('false_')) return false;
        }
    }
    
    // Check 2: Checkmark icons (only exist on outgoing messages)
    const hasCheckmark = msgDiv.querySelector('span[data-icon*="check"]') !== null || 
                         msgDiv.querySelector('span[data-icon*="dblcheck"]') !== null || 
                         msgDiv.querySelector('span[data-icon*="time"]') !== null;
    if (hasCheckmark) return true;
    
    // Check 3: CSS class wildcards
    if (msgDiv.closest('[class*="message-out"]') !== null || msgDiv.closest('.message-out') !== null) {
        return true;
    }
    
    // Check 4: Compare sender name with active chat name
    const chatName = getWhatsAppContact();
    if (chatName && chatName !== "Unknown") {
        const isGroup = checkIsGroupChat();
        if (!isGroup && sender && sender !== chatName) {
            return true;
        }
    }
    
    return false;
}

function parseWhatsAppDate(timeStr) {
    try {
        let normalized = timeStr.replace(/[\u200E\u200F]/g, "").trim(); // Remove left-to-right control chars
        let d = new Date(normalized);
        if (!isNaN(d.getTime())) return d;
        
        if (normalized.includes(',')) {
            const parts = normalized.split(',');
            let d2 = new Date(parts[1].trim() + ' ' + parts[0].trim());
            if (!isNaN(d2.getTime())) return d2;
            
            let d3 = new Date(parts[0].trim() + ' ' + parts[1].trim());
            if (!isNaN(d3.getTime())) return d3;
        }
        
        // Regex fallback for localized delimiters
        const dateMatch = normalized.match(/(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{2,4})/);
        const timeMatch = normalized.match(/(\d{1,2}):(\d{2})(?:\s*(AM|PM))?/i);
        if (dateMatch && timeMatch) {
            let year = parseInt(dateMatch[3]);
            if (year < 100) year += 2000;
            
            let p1 = parseInt(dateMatch[1]);
            let p2 = parseInt(dateMatch[2]);
            let month = p1;
            let day = p2;
            if (p1 > 12) {
                month = p2;
                day = p1;
            } else if (p2 > 12) {
                month = p1;
                day = p2;
            }
            
            let hours = parseInt(timeMatch[1]);
            const minutes = parseInt(timeMatch[2]);
            const ampm = timeMatch[3];
            if (ampm) {
                if (ampm.toUpperCase() === "PM" && hours < 12) hours += 12;
                if (ampm.toUpperCase() === "AM" && hours === 12) hours = 0;
            }
            return new Date(year, month - 1, day, hours, minutes);
        }
    } catch (err) {
        console.error("Error parsing WhatsApp date:", err);
    }
    return null;
}

function getLinkedInContact() {
    const chatHeader = document.querySelector('.msg-entity-lockup__title') || document.querySelector('.msg-thread__link');
    if (chatHeader) {
        return chatHeader.innerText.trim();
    }
    return "LinkedIn Contact";
}

function getInstagramContact() {
    const chatHeader = document.querySelector('span[x-class*="username"]') || document.querySelector('div[role="presentation"] h1') || document.querySelector('span.x1lliihq');
    if (chatHeader) {
        return chatHeader.innerText.trim();
    }
    return "Instagram User";
}

// --- STREAMING-SAFE RESPONSE CAPTURE ---
// Previously, ChatGPT/Gemini/Claude responses were read once via a single
// fixed setTimeout (1.5-2s) after the response container first appeared.
// That's a race against the model's own streaming: any answer that takes
// longer than the fixed delay got logged truncated/mid-sentence, and once
// logged the node was never re-read, so the final, complete answer was lost
// forever. This watcher instead observes the response container itself and
// only fires once its text has stopped changing for `debounceMs` (i.e. the
// stream has actually finished), with a `maxWaitMs` safety cap so a stalled
// stream can't hang the watcher indefinitely.
const _watchedNodes = new WeakSet();
function watchForStreamCompletion(containerEl, onComplete, { debounceMs = 1200, maxWaitMs = 30000 } = {}) {
    if (!containerEl || _watchedNodes.has(containerEl)) return;
    _watchedNodes.add(containerEl);

    const startedAt = Date.now();
    let debounceTimer = null;
    let localObserver = null;

    const finish = () => {
        clearTimeout(debounceTimer);
        if (localObserver) localObserver.disconnect();
        onComplete();
    };

    const scheduleCheck = () => {
        clearTimeout(debounceTimer);
        if (Date.now() - startedAt >= maxWaitMs) {
            finish();
            return;
        }
        debounceTimer = setTimeout(finish, debounceMs);
    };

    localObserver = new MutationObserver(scheduleCheck);
    localObserver.observe(containerEl, { childList: true, subtree: true, characterData: true });
    // Also cover the case where the response is already short/static and no
    // further mutations ever arrive.
    scheduleCheck();
}

// WhatsApp bubbles occasionally get their data-pre-plain-text metadata
// attribute set slightly *after* the node is inserted (media/caption
// messages), so relying only on "was this node just added" can miss them.
// Centralizing the logic lets both the childList and attribute observers
// share it.
function handleWhatsAppBubble(msgDiv) {
    if (!msgDiv || !msgDiv.hasAttribute || !msgDiv.hasAttribute('data-pre-plain-text')) return;
    const meta = msgDiv.getAttribute('data-pre-plain-text');
    
    // Filter out old/historical chats during initial message list render
    const timeMatch = meta.match(/^\[([^\]]+)\]/);
    if (timeMatch) {
        const msgDate = parseWhatsAppDate(timeMatch[1]);
        if (msgDate) {
            const diffMs = Math.abs(Date.now() - msgDate.getTime());
            if (diffMs > 120000) return; // Ignore messages older than 2 minutes
        }
    }

    const match = meta.match(/\]\s*(.*?):\s*$/);
    if (!match) return;

    let sender = match[1].trim();
    const isOutgoing = checkIsOutgoing(msgDiv, sender);
    if (isOutgoing) sender = "You";

    const textSpan = msgDiv.querySelector('span.selectable-text.copyable-text') || 
                     msgDiv.querySelector('span.selectable-text') || 
                     msgDiv.querySelector('.selectable-text') ||
                     msgDiv.querySelector('span.copyable-text');
    if (textSpan) {
        const text = textSpan.innerText;
        const chatName = getWhatsAppContact();
        sendWebLog("whatsapp", chatName, text, sender);
    }
}

// MutationObserver for incoming responses & page logs
const observer = new MutationObserver((mutations) => {
    const host = window.location.hostname;
    
    if (host.includes("meet.google.com")) {
        processMeetCaptions();
        return;
    }

    for (const mutation of mutations) {
        if (mutation.type === 'attributes' && host.includes("web.whatsapp.com")) {
            if (mutation.attributeName === 'data-pre-plain-text') {
                handleWhatsAppBubble(mutation.target);
            }
            continue;
        }

        if (mutation.type === 'childList') {
            for (const node of mutation.addedNodes) {
                if (node.nodeType !== Node.ELEMENT_NODE) continue;
                
                // WhatsApp Messages
                if (host.includes("web.whatsapp.com")) {
                    const msgDiv = node.classList.contains('copyable-text') ? node : node.querySelector('.copyable-text');
                    if (msgDiv) handleWhatsAppBubble(msgDiv);
                }
                
                // ChatGPT Responses
                if (host.includes("chatgpt.com")) {
                    const turn = node.querySelector('[data-testid^="conversation-turn-"]') || 
                                 (node.getAttribute && node.getAttribute('data-testid') && node.getAttribute('data-testid').startsWith('conversation-turn-') ? node : null);
                    if (turn) {
                        const isAssistant = turn.querySelector('[data-message-author-role="assistant"]');
                        if (isAssistant) {
                            const textDiv = turn.querySelector('.markdown');
                            if (textDiv) {
                                watchForStreamCompletion(textDiv, () => {
                                    const text = textDiv.innerText;
                                    let target = document.title.replace("ChatGPT - ", "").replace("ChatGPT", "").trim() || "New Chat";
                                    sendWebLog("chatgpt", target, "ChatGPT response: " + text, "ChatGPT");
                                });
                            }
                        }
                    }
                }

                // Gemini Responses
                if (host.includes("gemini.google.com")) {
                    const msgContent = node.querySelector('message-content') || (node.tagName === 'MESSAGE-CONTENT' ? node : null);
                    if (msgContent) {
                        watchForStreamCompletion(msgContent, () => {
                            const text = msgContent.innerText;
                            sendWebLog("gemini", "Gemini Chat", "Gemini response: " + text, "Gemini");
                        });
                    }
                }

                // Claude Responses
                if (host.includes("claude.ai")) {
                    const claudeMsg = node.querySelector('.font-claude-message') || (node.classList.contains('font-claude-message') ? node : null);
                    if (claudeMsg) {
                        watchForStreamCompletion(claudeMsg, () => {
                            const text = claudeMsg.innerText;
                            const target = document.title.replace("Claude", "").trim() || "Claude Chat";
                            sendWebLog("claude", target, "Claude response: " + text, "Claude");
                        });
                    }
                }

                // Grok Responses
                if (host.includes("grok.com")) {
                    const markdownEl = node.querySelector('.markdown') || (node.classList && node.classList.contains('markdown') ? node : null);
                    if (markdownEl) {
                        const isUserTurn = markdownEl.closest('.user-message') || markdownEl.closest('[class*="user"]');
                        if (!isUserTurn) {
                            watchForStreamCompletion(markdownEl, () => {
                                const text = markdownEl.innerText;
                                sendWebLog("grok", "Grok Chat", "Grok response: " + text, "Grok");
                            });
                        }
                    }
                }

                // Groq Responses
                if (host.includes("console.groq.com")) {
                    const assistantEl = node.querySelector('.assistant') || node.querySelector('[data-role="assistant"]') || 
                                        (node.classList && node.classList.contains('assistant') ? node : null);
                    if (assistantEl) {
                        watchForStreamCompletion(assistantEl, () => {
                            const text = assistantEl.innerText;
                            sendWebLog("groq", "Groq Playground", "Groq response: " + text, "Groq");
                        });
                    }
                }

                // LinkedIn Feed Posts Scraper
                if (host.includes("linkedin.com")) {
                    const post = node.querySelector('.feed-shared-update-v2') || (node.classList.contains('feed-shared-update-v2') ? node : null);
                    if (post) {
                        const actor = post.querySelector('.update-components-actor__title');
                        const body = post.querySelector('.update-components-text');
                        if (actor && body) {
                            const actorName = actor.innerText.trim();
                            const postText = body.innerText.trim();
                            sendWebLog("linkedin", "LinkedIn Feed", `Viewed LinkedIn post by ${actorName}: ${postText}`, actorName);
                        }
                    }
                }

                // Chess.com Match tracker (move list updates)
                if (host.includes("chess.com")) {
                    const moveItem = node.querySelector('.move-list-item') || (node.classList.contains('move-list-item') ? node : null);
                    if (moveItem) {
                        const moves = moveItem.innerText.replace(/\n/g, " ").trim();
                        sendWebLog("chess", "Chess.com Game", "Match moves logged: " + moves, "System");
                    }
                }
            }
        }
    }
});
observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-pre-plain-text'] });

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
        
        let speaker = "Speaker";
        const nameEl = block.querySelector('.zs7s8d') || block.querySelector('.jmjoTe') || block.firstElementChild;
        if (nameEl && nameEl.innerText.trim()) {
            speaker = nameEl.innerText.trim();
        }
        state.speaker = speaker;
        
        let speechText = "";
        const textEl = block.querySelector('.iTTPOb') || block.querySelector('.jTuls') || block.querySelector('span') || block.lastElementChild;
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

// --- SPA URL HISTORY CHANGE LISTENER ---
let lastUrl = window.location.href;
function handleUrlChange() {
    const url = window.location.href;
    const host = window.location.hostname;
    const path = window.location.pathname;

    console.log(`Qareen URL change detected: ${url}`);

    // Initial page load triggers for read-only platforms
    if (host.includes("mail.google.com")) {
        // Gmail email viewer logger
        // Wait for thread to load
        setTimeout(() => {
            const subjectEl = document.querySelector('h2.hP');
            const senderEl = document.querySelector('.gD');
            const bodyEl = document.querySelector('.a3s.aiL') || document.querySelector('div[role="main"]');
            if (subjectEl && bodyEl) {
                const subject = subjectEl.innerText.trim();
                const sender = senderEl ? (senderEl.getAttribute('email') || senderEl.innerText) : "Unknown Sender";
                const bodySnippet = bodyEl.innerText.substring(0, 500).trim();
                sendWebLog("gmail", sender, `Read Email (Subject: ${subject}): ${bodySnippet}`, sender);
            }
        }, 2000);
    } else if (host.includes("reddit.com")) {
        // Reddit post reader
        if (path.includes("/comments/")) {
            setTimeout(() => {
                const sub = path.split('/r/')[1]?.split('/')[0] || "Reddit";
                const titleEl = document.querySelector('shreddit-title') || document.querySelector('h1');
                const title = titleEl ? (titleEl.getAttribute('title') || titleEl.innerText) : "Reddit Post";
                const bodyEl = document.querySelector('div[data-click-id="text_body"]') || document.querySelector('.text-neutral-content');
                const body = bodyEl ? bodyEl.innerText.substring(0, 800) : "";
                sendWebLog("reddit", `r/${sub}`, `Read Reddit Post "${title}": ${body}`, "Reddit");
            }, 2000);
        }
    } else if (host.includes("github.com")) {
        // GitHub repo / file views
        const segments = path.split('/').filter(Boolean);
        if (segments.length >= 2) {
            const owner = segments[0];
            const repo = segments[1];
            const type = segments[2] || "root";
            
            if (type === "blob") {
                const filename = segments.slice(4).join('/');
                sendWebLog("github", `${owner}/${repo}`, `Viewed file: ${filename} in GitHub repository ${owner}/${repo}`, "You");
            } else if (type === "pull" || type === "issues") {
                setTimeout(() => {
                    const titleEl = document.querySelector('.gh-header-title');
                    const title = titleEl ? titleEl.innerText.trim() : "Issue/PR";
                    sendWebLog("github", `${owner}/${repo}`, `Viewed ${type === "pull" ? "Pull Request" : "Issue"} #${segments[3]}: ${title}`, "GitHub");
                }, 2000);
            } else if (segments.length === 2) {
                sendWebLog("github", `${owner}/${repo}`, `Visited GitHub repository: ${owner}/${repo}`, "You");
            }
        }
    } else if (host.includes("instagram.com")) {
        // Instagram post / reel views
        if (path.startsWith("/p/") || path.startsWith("/reel/")) {
            setTimeout(() => {
                const authorEl = document.querySelector('a.x1i10hfl[role="link"]');
                const author = authorEl ? authorEl.innerText.trim() : "Instagram Account";
                const descEl = document.querySelector('._ap3a') || document.querySelector('h1');
                const desc = descEl ? descEl.innerText.substring(0, 500) : "";
                sendWebLog("instagram", author, `Viewed Instagram post/reel: ${desc}`, author);
            }, 2500);
        }
    } else if (host.includes("chess.com")) {
        if (path.includes("/game/") || path.includes("/play/")) {
            setTimeout(() => {
                const opponentEl = document.querySelector('.player-avatar') || document.querySelector('.player-username-link');
                const oppName = opponentEl ? opponentEl.innerText.trim() : "Opponent";
                sendWebLog("chess", "Chess.com Game", `Started / resumed chess match against ${oppName}`, "System");
            }, 3000);
        }
    } else if (host.includes("chatgpt.com")) {
        setTimeout(() => {
            let target = document.title.replace("ChatGPT - ", "").replace("ChatGPT", "").trim() || "New Chat";
            sendWebLog("chatgpt", target, `Opened ChatGPT chat: ${target} (URL: ${url})`, "You");
        }, 2000);
    } else if (host.includes("web.whatsapp.com")) {
        setTimeout(() => {
            const hasChat = document.querySelector('#main') !== null;
            if (hasChat) {
                let target = getWhatsAppContact();
                sendWebLog("whatsapp", target, `Opened WhatsApp chat with: ${target}`, "You");
            } else {
                sendWebLog("whatsapp", "WhatsApp Web", `Opened WhatsApp Web`, "System");
            }
        }, 4000);
    } else if (host.includes("lichess.org")) {
        const segments = path.split('/').filter(Boolean);
        if (segments.length === 1 && segments[0].length >= 8) {
            setTimeout(() => {
                const oppEl = document.querySelector('.user-link') || document.querySelector('.player .name');
                const oppName = oppEl ? oppEl.innerText.trim() : "Opponent";
                sendWebLog("chess", "Lichess Game", `Started / resumed chess match on Lichess against ${oppName}`, "System");
            }, 3000);
        }
    } else if (!host.includes("chatgpt.com") && !host.includes("web.whatsapp.com") && !host.includes("meet.google.com")) {
        // Log basic page view on load/change
        const title = document.title || host;
        sendWebLog("browse", host, `Visited webpage: ${title} (URL: ${url})`, "You");
    }
}

let lastFinishedGameUrl = "";

function checkChessGameStatus() {
    const host = window.location.hostname;
    const url = window.location.href;
    
    if (url === lastFinishedGameUrl) return;

    if (host.includes("chess.com")) {
        const modalHeader = document.querySelector('.game-over-header-component') || 
                             document.querySelector('.game-over-header-title') ||
                             document.querySelector('.game-over-status') ||
                             document.querySelector('.game-over-modal-header');
        if (modalHeader) {
            const statusText = modalHeader.innerText.trim();
            if (statusText) {
                const reasonEl = document.querySelector('.game-over-reason') || document.querySelector('.game-over-modal-body');
                const reasonText = reasonEl ? reasonEl.innerText.trim().replace(/\n/g, " ") : "";
                const fullResult = reasonText ? `${statusText} (${reasonText})` : statusText;
                
                sendWebLog("chess", "Chess.com Game", `Chess.com match finished: ${fullResult}`, "System");
                lastFinishedGameUrl = url;
            }
        }
    } else if (host.includes("lichess.org")) {
        const statusEl = document.querySelector('.game__meta .status') || 
                         document.querySelector('.round__side .status') ||
                         document.querySelector('.round__app .result') ||
                         document.querySelector('.status');
        if (statusEl) {
            const statusText = statusEl.innerText.trim().replace(/\n/g, " ");
            const lower = statusText.toLowerCase();
            const finishedKeywords = ["victorious", "victory", "drawn", "draw", "mate", "resign", "abort", "flag", "stalemate", "timeout", "defeat"];
            const isFinished = finishedKeywords.some(kw => lower.includes(kw));
            if (isFinished) {
                sendWebLog("chess", "Lichess Game", `Lichess match finished: ${statusText}`, "System");
                lastFinishedGameUrl = url;
            }
        }
    }
}

// Poller for URL changes in SPA and Chess game status checks
setInterval(() => {
    if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        handleUrlChange();
    }
    const host = window.location.hostname;
    if (host.includes("chess.com") || host.includes("lichess.org")) {
        checkChessGameStatus();
    }
}, 2000);

// Run initial logic
setTimeout(handleUrlChange, 1000);
