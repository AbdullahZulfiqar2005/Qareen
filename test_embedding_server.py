import struct

from embedding_server import format_web_event, pack_embedding


class TestPackEmbedding:
    def test_round_trip(self):
        original = [float(i) * 0.01 for i in range(384)]
        blob = pack_embedding(original)
        unpacked = list(struct.unpack(f"<{len(blob)//4}f", blob))
        assert len(unpacked) == 384
        for orig, got in zip(original, unpacked):
            assert abs(orig - got) < 1e-6

    def test_output_size(self):
        embedding = [0.0] * 384
        blob = pack_embedding(embedding)
        assert len(blob) == 384 * 4  # 1536 bytes

    def test_known_values(self):
        embedding = [1.0, -1.0, 0.5]
        blob = pack_embedding(embedding)
        vals = struct.unpack("<3f", blob)
        assert abs(vals[0] - 1.0) < 1e-6
        assert abs(vals[1] - (-1.0)) < 1e-6
        assert abs(vals[2] - 0.5) < 1e-6

    def test_empty(self):
        blob = pack_embedding([])
        assert len(blob) == 0


class TestFormatWebEvent:
    def test_chatgpt_prompt(self):
        result = format_web_event("chatgpt", "My Chat", "what is Python?", "You")
        assert result == "Prompted ChatGPT in conversation 'My Chat': what is Python?"

    def test_chatgpt_response(self):
        result = format_web_event("chatgpt", "My Chat", "ChatGPT response: Python is a language", "")
        assert result == "ChatGPT replied in conversation 'My Chat': Python is a language"

    def test_claude_prompt(self):
        result = format_web_event("claude", "New Chat", "explain recursion", "You")
        assert result == "Prompted Claude in conversation 'New Chat': explain recursion"

    def test_claude_response(self):
        result = format_web_event("claude", "New Chat", "Claude response: Recursion is...", "")
        assert result == "Claude replied in conversation 'New Chat': Recursion is..."

    def test_gemini_prompt(self):
        result = format_web_event("gemini", "Gemini Chat", "hello", "You")
        assert result == "Prompted Gemini in conversation 'Gemini Chat': hello"

    def test_gemini_response(self):
        result = format_web_event("gemini", "Gemini Chat", "Gemini response: Hi there", "")
        assert result == "Gemini replied in conversation 'Gemini Chat': Hi there"

    def test_grok_prompt(self):
        result = format_web_event("grok", "Grok Chat", "what's up", "You")
        assert result == "Prompted Grok in conversation 'Grok Chat': what's up"

    def test_whatsapp_outgoing(self):
        result = format_web_event("whatsapp", "Alice", "hey there", "You")
        assert result == "Sent WhatsApp message to Alice: hey there"

    def test_whatsapp_outgoing_no_sender(self):
        result = format_web_event("whatsapp", "Alice", "hey there", "")
        assert result == "Sent WhatsApp message to Alice: hey there"

    def test_whatsapp_incoming_direct(self):
        result = format_web_event("whatsapp", "Bob", "hi!", "Bob")
        assert result == "Received WhatsApp message from Bob: hi!"

    def test_whatsapp_incoming_group(self):
        result = format_web_event("whatsapp", "Work Group", "meeting at 3", "Charlie")
        assert result == "Received WhatsApp message from Charlie in group 'Work Group': meeting at 3"

    def test_google_meet(self):
        result = format_web_event("google-meet", "meeting", "let's discuss", "John")
        assert result == "Google Meet meeting transcript: John said: let's discuss"

    def test_linkedin(self):
        result = format_web_event("linkedin", "Jane Doe", "interested in your work", "You")
        assert result == "Sent LinkedIn message to Jane Doe: interested in your work"

    def test_instagram(self):
        result = format_web_event("instagram", "user123", "nice photo!", "You")
        assert result == "Sent Instagram DM to user123: nice photo!"

    def test_reddit_passthrough(self):
        content = "Posted comment: this is great"
        result = format_web_event("reddit", "r/golang", content, "You")
        assert result == content

    def test_github_passthrough(self):
        content = "Posted comment on GitHub: looks good"
        result = format_web_event("github", "repo/issue", content, "You")
        assert result == content

    def test_gmail_passthrough(self):
        content = "Sent Email (Subject: Hello): body text here"
        result = format_web_event("gmail", "user@example.com", content, "You")
        assert result == content

    def test_unknown_site(self):
        result = format_web_event("stackoverflow", "question/123", "viewed answer", "You")
        assert result == "Web event on stackoverflow: viewed answer"

    def test_chess(self):
        result = format_web_event("chess", "game", "e2-e4", "You")
        assert result == "Web event on chess: e2-e4"
