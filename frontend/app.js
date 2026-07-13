// App controller — topic screen, deck rendering, WebSocket relay (with
// reconnect), audio playback, mic, Present Mode, and .pptx upload. Phases 4–11.

(function () {
  // ---- Elements ----
  const topicScreen = document.getElementById("topic-screen");
  const deckScreen = document.getElementById("deck-screen");
  const topicForm = document.getElementById("topic-form");
  const topicInput = document.getElementById("topic-input");
  const topicMsg = document.getElementById("topic-msg");
  const generateBtn = document.getElementById("generate-btn");
  const fileInput = document.getElementById("file-input");
  const uploadLabel = document.getElementById("upload-label");

  const deckTitle = document.getElementById("deck-title");
  const newTopicBtn = document.getElementById("new-topic");
  const presentBtn = document.getElementById("present-btn");
  const pauseBtn = document.getElementById("pause-btn");
  const stopBtn = document.getElementById("stop-btn");
  const el = {
    kicker: document.getElementById("kicker"),
    title: document.getElementById("title"),
    bullets: document.getElementById("bullets"),
    note: document.getElementById("note"),
    current: document.getElementById("current"),
    total: document.getElementById("total"),
    dots: document.getElementById("dots"),
    slide: document.getElementById("slide"),
    prev: document.getElementById("prev"),
    next: document.getElementById("next"),
  };

  const conn = document.getElementById("conn");
  const form = document.getElementById("ask");
  const input = document.getElementById("ask-input");
  const answerBox = document.getElementById("answer");
  const answerText = document.getElementById("answer-text");
  const micBtn = document.getElementById("mic-btn");
  const heard = document.getElementById("heard");
  const muteChk = document.getElementById("mute-mic");
  const deckMsg = document.getElementById("deck-msg");

  let deckMsgTimer = null;
  function showDeckMsg(text) {
    deckMsg.textContent = text;
    deckMsg.hidden = false;
    if (deckMsgTimer) clearTimeout(deckMsgTimer);
    deckMsgTimer = setTimeout(() => { deckMsg.hidden = true; }, 6000);
  }

  // ---- Deck state ----
  let slides = [];
  let index = 0;
  let currentDeck = null; // cached so we can re-apply after a reconnect

  // ---- Present + speaking state ----
  let presenting = false;
  let presentPaused = false;
  let presentTimer = null;
  let aiSpeaking = false;
  function clearPresentTimer() {
    if (presentTimer) { clearTimeout(presentTimer); presentTimer = null; }
  }
  function updatePresentUI() {
    presentBtn.hidden = presenting;
    pauseBtn.hidden = !presenting;
    stopBtn.hidden = !presenting;
    pauseBtn.textContent = presentPaused ? "▶ Resume" : "⏸ Pause";
  }

  // ---- Deck rendering ----
  function renderDots() {
    el.dots.innerHTML = "";
    slides.forEach((_, i) => {
      const dot = document.createElement("span");
      dot.className = "dot";
      dot.addEventListener("click", () => goTo(i));
      el.dots.appendChild(dot);
    });
  }
  function render() {
    const s = slides[index];
    if (!s) return;
    el.kicker.textContent = `Slide ${index + 1} of ${slides.length}`;
    el.title.textContent = s.title;
    el.note.textContent = s.note || "";
    el.current.textContent = index + 1;
    el.total.textContent = slides.length;
    el.bullets.innerHTML = "";
    (s.bullets || []).forEach((b) => {
      const li = document.createElement("li");
      li.textContent = b;
      el.bullets.appendChild(li);
    });
    [...el.dots.children].forEach((d, i) => d.classList.toggle("active", i === index));
    el.prev.disabled = index === 0;
    el.next.disabled = index === slides.length - 1;
    el.slide.classList.remove("enter");
    void el.slide.offsetWidth;
    el.slide.classList.add("enter");
  }
  function goTo(n) {
    index = Math.max(0, Math.min(slides.length - 1, n));
    render();
  }
  window.goToSlide = (oneBased) => goTo(oneBased - 1); // backend slide events

  function loadDeck(title, deckSlides) {
    slides = deckSlides;
    index = 0;
    currentDeck = { title, slides: deckSlides };
    deckTitle.textContent = title;
    renderDots();
    render();
    topicScreen.hidden = true;
    deckScreen.hidden = false;
    presenting = false;
    presentPaused = false;
    clearPresentTimer();
    updatePresentUI();
    input.focus();
  }

  el.prev.addEventListener("click", () => goTo(index - 1));
  el.next.addEventListener("click", () => goTo(index + 1));
  document.addEventListener("keydown", (e) => {
    if (deckScreen.hidden) return;
    if (e.key === "ArrowRight") goTo(index + 1);
    if (e.key === "ArrowLeft") goTo(index - 1);
  });
  newTopicBtn.addEventListener("click", () => location.reload());

  // ---- WebSocket relay (with auto-reconnect) ----
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  let ws;
  let reconnectDelay = 1000;

  function connect() {
    ws = new WebSocket(url);

    ws.addEventListener("open", () => {
      conn.textContent = "◉ connected";
      conn.classList.add("live");
      reconnectDelay = 1000;
      // Survive the Realtime session limit / drops: re-apply our deck.
      if (currentDeck) {
        ws.send(JSON.stringify({ type: "use_deck", title: currentDeck.title, slides: currentDeck.slides }));
      }
    });

    ws.addEventListener("message", onMessage);

    ws.addEventListener("close", () => {
      conn.textContent = "◍ reconnecting…";
      conn.classList.remove("live");
      presenting = false; presentPaused = false; clearPresentTimer();
      updatePresentUI();
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.5, 8000);
    });

    ws.addEventListener("error", () => { try { ws.close(); } catch (_) {} });
  }

  function onMessage(ev) {
    const data = JSON.parse(ev.data);

    switch (data.type) {
      case "status":
        if (!presenting) conn.textContent = "◉ " + data.msg;
        break;
      case "deck":
        setGenerating(false);
        loadDeck(data.title, data.slides);
        break;
      case "generation_error":
        setGenerating(false);
        topicMsg.textContent = "⚠ " + data.msg;
        break;
      case "slide":
        window.goToSlide(data.n);
        break;
      case "audio_delta":
        if (!aiSpeaking) {
          aiSpeaking = true;
          if (!presenting) conn.textContent = "◉ speaking…";
        }
        window.pcmPlayer.play(data.audio);
        break;
      case "answer_delta":
        answerBox.hidden = false;
        answerText.textContent += data.delta;
        break;
      case "answer_done":
        aiSpeaking = false;
        input.disabled = false;
        if (!window.micStreamer.isActive && !presenting) input.focus();
        if (presenting && !presentPaused) {
          const waitMs = window.pcmPlayer.remainingSeconds() * 1000 + 400;
          clearPresentTimer();
          presentTimer = setTimeout(() => {
            if (presenting && !presentPaused && ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: "playback_idle" }));
            }
          }, waitMs);
        }
        break;
      case "presenting":
        conn.textContent = `◉ presenting slide ${data.index} / ${data.total}`;
        break;
      case "present_done":
        presenting = false; presentPaused = false;
        clearPresentTimer(); updatePresentUI();
        break;
      case "user_speaking":
        aiSpeaking = false;
        window.pcmPlayer.stop();  // barge-in: cut the AI off
        clearPresentTimer();      // the Q&A answer will pace any resume
        conn.textContent = "◉ listening…";
        answerText.textContent = "";
        break;
      case "user_transcript":
        heard.hidden = false;
        heard.textContent = "You said: " + data.text;
        break;
      case "error":
        aiSpeaking = false;
        setGenerating(false);
        input.disabled = false;
        if (!topicScreen.hidden) topicMsg.textContent = "⚠ " + data.msg;
        else showDeckMsg("⚠ " + data.msg);
        break;
    }
  }

  connect();

  // ---- Topic generation ----
  let generating = false;
  function setGenerating(on) {
    generating = on;
    generateBtn.disabled = on;
    topicInput.disabled = on;
    if (fileInput) fileInput.disabled = on;
    generateBtn.textContent = on ? "Generating…" : "Generate & Present";
    if (on) topicMsg.textContent = "";
    topicScreen.classList.toggle("busy", on);
  }

  topicForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const topic = topicInput.value.trim();
    if (topic.length < 2) { topicMsg.textContent = "⚠ Please enter a topic."; return; }
    if (generating || ws.readyState !== WebSocket.OPEN) return;
    window.pcmPlayer.ensure();
    setGenerating(true);
    ws.send(JSON.stringify({ type: "generate", topic }));
  });

  // ---- Upload a .pptx ----
  fileInput.addEventListener("change", async () => {
    const f = fileInput.files[0];
    if (!f) return;
    if (ws.readyState !== WebSocket.OPEN) { topicMsg.textContent = "⚠ Not connected yet."; return; }
    window.pcmPlayer.ensure();
    setGenerating(true);
    uploadLabel.textContent = "Reading .pptx…";
    try {
      const fd = new FormData();
      fd.append("file", f);
      const res = await fetch("/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload failed.");
      // Hand the parsed deck to the backend to configure the session.
      ws.send(JSON.stringify({ type: "use_deck", title: data.title, slides: data.slides }));
      // The 'deck' message will flip us into the deck view.
    } catch (err) {
      setGenerating(false);
      topicMsg.textContent = "⚠ " + err.message;
    } finally {
      fileInput.value = "";
      uploadLabel.childNodes[0].nodeValue = "📄 Upload a .pptx ";
    }
  });

  // ---- Ask (typed) ----
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || ws.readyState !== WebSocket.OPEN) return;
    window.pcmPlayer.ensure();
    answerBox.hidden = false;
    answerText.textContent = "";
    ws.send(JSON.stringify({ type: "user_text", text }));
    input.value = "";
    input.disabled = true;
  });

  // ---- Present Mode controls ----
  presentBtn.addEventListener("click", () => {
    if (presenting || ws.readyState !== WebSocket.OPEN) return;
    window.pcmPlayer.ensure();
    presenting = true; presentPaused = false;
    updatePresentUI();
    ws.send(JSON.stringify({ type: "present_start" }));
  });
  pauseBtn.addEventListener("click", () => {
    if (!presenting) return;
    presentPaused = !presentPaused;
    if (presentPaused) {
      clearPresentTimer();
      window.pcmPlayer.stop();
      ws.send(JSON.stringify({ type: "present_pause" }));
    } else {
      ws.send(JSON.stringify({ type: "present_resume" }));
    }
    updatePresentUI();
  });
  stopBtn.addEventListener("click", () => {
    if (!presenting) return;
    presenting = false; presentPaused = false;
    clearPresentTimer();
    window.pcmPlayer.stop();
    updatePresentUI();
    ws.send(JSON.stringify({ type: "present_stop" }));
  });

  // ---- Mic (voice) ----
  micBtn.addEventListener("click", async () => {
    if (window.micStreamer.isActive) {
      window.micStreamer.stop();
      micBtn.classList.remove("recording");
      micBtn.textContent = "🎤 Talk";
      conn.textContent = "◉ mic off";
      return;
    }
    try {
      window.pcmPlayer.ensure();
      await window.micStreamer.start((b64) => {
        // Token saver / no-headphones: optionally don't send while AI speaks.
        if (muteChk.checked && aiSpeaking) return;
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "audio_in", audio: b64 }));
        }
      });
      micBtn.classList.add("recording");
      micBtn.textContent = "■ Stop";
      conn.textContent = "◉ mic on — just speak";
    } catch (err) {
      console.error(err);
      conn.textContent = "◍ mic blocked (allow microphone access)";
    }
  });
})();
