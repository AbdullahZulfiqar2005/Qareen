console.log("Qareen Background Script Initialized!");

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === "logWeb") {
        console.log("Qareen Background received log message:", message);
        
        fetch("http://127.0.0.1:2846/log-web", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                site: message.site,
                target: message.target,
                content: message.content,
                sender: message.sender
            })
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            console.log("Log saved successfully to Qareen database:", data);
        })
        .catch(err => {
            console.error("Failed to forward log to local Qareen server:", err);
        });
    }
});
