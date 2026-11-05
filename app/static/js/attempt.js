// Exam-taking page.
//
// Timing is server-authoritative: the server sends "remaining_seconds" and the page counts down with
// performance.now(), a monotonic clock that ignores changes to the computer's date/time. The page
// re-syncs with the server periodically and whenever the tab becomes visible again.
(() => {
  "use strict";

  const root = document.getElementById("attempt");
  const attemptId = root.dataset.attemptId;
  const els = {
    questions: document.getElementById("questions"),
    palette: document.getElementById("palette"),
    timer: document.getElementById("timer"),
    answered: document.getElementById("answered-count"),
    total: document.getElementById("total-count"),
    timeUp: document.getElementById("time-up"),
    error: document.getElementById("load-error"),
    submit: document.getElementById("submit-btn"),
    saveStatus: document.getElementById("save-status"),
  };

  const RESYNC_MS = 30_000;
  const answers = {}; // { questionId: choiceId }
  let deadline = null; // performance.now() value at which time runs out
  let locked = false;
  let submitting = false;
  let questionCount = 0;
  const questionsById = {};
  const flags = new Set(); // question ids marked "unsure" (🚩)
  let flagsDirty = false; // flags changed since the last successful sync
  const times = {}; // seconds spent per question id (estimate: the question in view or last touched)
  let timesDirty = false;
  let activeId = null;

  // ------------------------------------------------------------------ server clock

  async function fetchJson(url) {
    const res = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" });
    if (res.status === 401) {
      window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
      throw new Error("unauthenticated");
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  // Adopt the server's remaining time, compensating for half the round trip.
  function setDeadline(remainingSeconds, sentAt, receivedAt) {
    const oneWay = (receivedAt - sentAt) / 2;
    deadline = receivedAt + remainingSeconds * 1000 - oneWay;
  }

  async function resync() {
    if (locked) return;
    try {
      const sentAt = performance.now();
      const data = await fetchJson(`/api/attempts/${attemptId}/clock`);
      setDeadline(data.remaining_seconds, sentAt, performance.now());
      if (data.status !== "IN_PROGRESS") lock();
    } catch (err) {
      console.warn("Clock resync failed", err); // keep counting down locally
    }
  }

  function tick() {
    if (deadline === null) return;
    const left = Math.max(0, (deadline - performance.now()) / 1000);
    const total = Math.ceil(left);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, "0");
    els.timer.textContent = (h ? h + ":" + pad(m) : pad(m)) + ":" + pad(s);
    els.timer.classList.toggle("warn", left <= 300 && left > 60);
    els.timer.classList.toggle("danger", left <= 60);
    if (left <= 0) lock();
  }

  // Time is up: freeze the page and hand the answers in automatically.
  function lock() {
    if (locked) return;
    locked = true;
    els.timer.textContent = "00:00";
    els.timeUp.hidden = false;
    root.classList.add("locked");
    root.querySelectorAll("#questions input").forEach((input) => (input.disabled = true));
    submitAnswers();
  }


  // ------------------------------------------------------------------ auto-save (2 layers)
  //
  // Layer 1: every change is written to IndexedDB immediately, so a crash, F5 or power cut loses nothing.
  // Layer 2: changed questions are sent to the server in the background (debounced), one request at a time.
  // On load, the server copy is the base and unsynced local changes are re-applied on top.

  const SYNC_DEBOUNCE_MS = 2000;
  const SYNC_MAX_WAIT_MS = 10000;
  const pending = {}; // { questionId: value | null } changed since the last successful sync
  let syncTimer = null;
  let firstPendingAt = null;
  let syncing = false;
  let retryDelay = 2000;

  const local = (() => {
    let dbPromise = null;
    function open() {
      if (!("indexedDB" in window)) return Promise.resolve(null);
      dbPromise = dbPromise || new Promise((resolve) => {
        const req = indexedDB.open("exam-autosave", 1);
        req.onupgradeneeded = () => req.result.createObjectStore("attempts");
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(null); // private mode etc.: fall back to server-only saving
      });
      return dbPromise;
    }
    async function tx(mode, fn) {
      const db = await open();
      if (!db) return undefined;
      return new Promise((resolve) => {
        const store = db.transaction("attempts", mode).objectStore("attempts");
        const req = fn(store);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(undefined);
      });
    }
    return {
      load: () => tx("readonly", (s) => s.get(attemptId)),
      save: () =>
        tx("readwrite", (s) =>
          s.put({ answers, pending, flags: [...flags], flagsDirty, times, updatedAt: Date.now() }, attemptId)
        ),
      clear: () => tx("readwrite", (s) => s.delete(attemptId)),
    };
  })();

  function setSaveStatus(state, text) {
    els.saveStatus.dataset.state = state;
    els.saveStatus.textContent = text;
  }

  function scheduleSync() {
    if (firstPendingAt === null) firstPendingAt = Date.now();
    clearTimeout(syncTimer);
    const wait = Math.max(0, Math.min(SYNC_DEBOUNCE_MS, firstPendingAt + SYNC_MAX_WAIT_MS - Date.now()));
    syncTimer = setTimeout(() => syncNow(), wait);
  }

  async function syncNow(keepalive = false) {
    if (syncing || submitting || (!Object.keys(pending).length && !flagsDirty && !timesDirty)) return;
    syncing = true;
    syncTimer = null;
    const batch = Object.assign({}, pending);
    const sendFlags = flagsDirty;
    flagsDirty = false;
    const sendTimes = timesDirty;
    timesDirty = false;
    setSaveStatus("saving", "Đang lưu…");
    try {
      const res = await fetch(`/api/attempts/${attemptId}/answers`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          changes: batch,
          ...(sendFlags ? { flags: [...flags] } : {}),
          ...(sendTimes ? { times } : {}),
        }),
        cache: "no-store",
        keepalive,
      });
      if (res.status === 409) { // submitted elsewhere or time is up: the server has the final word
        const data = await res.json();
        submitting = true;
        await local.clear();
        window.location.href = data.result_url;
        return;
      }
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      for (const [qid, value] of Object.entries(batch)) {
        if (JSON.stringify(pending[qid]) === JSON.stringify(value)) delete pending[qid]; // unchanged since sent
      }
      firstPendingAt = null;
      retryDelay = 2000;
      local.save();
      const time = new Date().toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      setSaveStatus("saved", "✓ Đã lưu lúc " + time);
      if (typeof data.remaining_seconds === "number") deadline = performance.now() + data.remaining_seconds * 1000;
    } catch (err) {
      if (sendFlags) flagsDirty = true;
      if (sendTimes) timesDirty = true;
      setSaveStatus("offline", "⚠ Mất kết nối, đáp án vẫn được giữ trên máy và sẽ tự lưu lại");
      clearTimeout(syncTimer);
      syncTimer = setTimeout(() => syncNow(), retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    } finally {
      syncing = false;
      if ((Object.keys(pending).length || flagsDirty) && !syncTimer) scheduleSync();
    }
  }

  function toggleFlag(questionId, button) {
    if (locked) return;
    const id = String(questionId);
    if (flags.has(id)) flags.delete(id);
    else flags.add(id);
    button.setAttribute("aria-pressed", flags.has(id) ? "true" : "false");
    button.textContent = flags.has(id) ? "🚩 Đã cắm cờ" : "🚩 Phân vân";
    flagsDirty = true;
    local.save();
    scheduleSync();
    refreshProgress();
  }

  function recordChange(questionId, value) {
    pending[questionId] = value;
    local.save();
    scheduleSync();
  }

  window.addEventListener("online", () => syncNow());
  // Leaving the page (closing the tab, switching apps on a phone): flush what is left.
  window.addEventListener("pagehide", () => syncNow(true));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") syncNow(true);
  });

  // ------------------------------------------------------------------ submit

  async function submitAnswers() {
    if (submitting) return;
    submitting = true;
    els.submit.disabled = true;
    els.submit.textContent = "Đang nộp bài…";
    // Retry a few times: a flaky connection at the deadline must not lose the attempt.
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const res = await fetch(`/api/attempts/${attemptId}/submit`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ answers, flags: [...flags], times }),
          cache: "no-store",
        });
        if (res.status === 401) {
          window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
          return;
        }
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        await local.clear();
        window.location.href = data.result_url;
        return;
      } catch (err) {
        console.warn("Submit failed, retrying", err);
        await new Promise((r) => setTimeout(r, 1000 * 2 ** attempt));
      }
    }
    submitting = false;
    els.submit.disabled = false;
    els.submit.textContent = "📤 Nộp lại";
    els.error.textContent = "Không nộp được bài do lỗi kết nối. Hãy kiểm tra mạng rồi bấm “Nộp lại”.";
    els.error.hidden = false;
  }

  // Submit dialog: lists blank and flagged questions; clicking a number jumps to it.
  const dialog = document.getElementById("submit-dialog");
  function jumpList(container, ids) {
    container.replaceChildren(
      ...ids.map((id) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = questionsById[id].number;
        btn.addEventListener("click", () => {
          dialog.close();
          document.getElementById("q-" + id).scrollIntoView({ behavior: "smooth", block: "start" });
        });
        return btn;
      })
    );
  }
  els.submit.addEventListener("click", () => {
    const ids = Object.keys(questionsById);
    const blank = ids.filter((id) => answerState(id) !== "answered");
    const flagged = ids.filter((id) => flags.has(id));
    dialog.querySelector(".blank-block").hidden = !blank.length;
    dialog.querySelector(".flag-block").hidden = !flagged.length;
    dialog.querySelector(".all-done").hidden = Boolean(blank.length || flagged.length);
    dialog.querySelector(".blank-count").textContent = blank.length;
    dialog.querySelector(".flag-count").textContent = flagged.length;
    jumpList(dialog.querySelector(".jump-list.blanks"), blank);
    jumpList(dialog.querySelector(".jump-list.flags"), flagged);
    dialog.showModal();
  });
  dialog.querySelector("[data-action=back]").addEventListener("click", () => dialog.close());
  dialog.querySelector("[data-action=submit]").addEventListener("click", () => {
    dialog.close();
    submitAnswers();
  });


  // ------------------------------------------------------------------ answer widgets (one per question type)

  const TYPE_LABELS = {
    MULTIPLE_CHOICE: "Trắc nghiệm",
    TRUE_FALSE: "Đúng/Sai",
    SHORT_ANSWER: "Trả lời ngắn",
    ESSAY: "Tự luận",
  };

  // Tự luận: a worked answer, graded by AI after submitting. LaTeX between $...$ is previewed live.
  function renderEssay(q, saved) {
    const wrap = document.createElement("div");
    const input = document.createElement("textarea");
    input.className = "essay-input";
    input.maxLength = 5000;
    input.placeholder = "Trình bày lời giải: tóm tắt, công thức, thay số, kết quả. Công thức viết trong $...$, VD: $v = \\omega A$";
    input.value = (saved && saved[q.id]) || "";
    const preview = document.createElement("div");
    preview.className = "essay-preview q-content";
    let timer;
    function showPreview() {
      preview.textContent = input.value;
      preview.hidden = !input.value.includes("$");
      if (!preview.hidden && window.renderMath) window.renderMath(preview);
    }
    input.addEventListener("input", () => {
      setAnswer(q.id, input.value.trim() ? input.value : null);
      clearTimeout(timer);
      timer = setTimeout(showPreview, 300);
    });
    showPreview();
    wrap.append(input, preview);
    return wrap;
  }

  function renderChoices(q, saved) {
    const list = document.createElement("div");
    list.className = "choice-list";
    q.choices.forEach((c, i) => {
      const label = document.createElement("label");
      label.className = "choice";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "q-" + q.id;
      input.value = c.id;
      input.checked = saved && saved[q.id] === c.id;
      input.addEventListener("change", () => setAnswer(q.id, c.id));
      const letter = document.createElement("span");
      letter.className = "choice-letter";
      letter.textContent = String.fromCharCode(65 + i);
      const text = document.createElement("span");
      text.className = "choice-text";
      text.textContent = c.content;
      label.append(input, letter, text);
      list.append(label);
    });
    return list;
  }

  // Phần II: each statement is marked true or false independently; partial credit is given.
  function renderTrueFalse(q, saved) {
    const table = document.createElement("table");
    table.className = "tf-table";
    const head = table.createTHead().insertRow();
    ["", "Ý", "Đúng", "Sai"].forEach((t) => {
      const th = document.createElement("th");
      th.textContent = t;
      head.append(th);
    });
    const body = table.createTBody();
    const current = (saved && saved[q.id]) || {};
    q.choices.forEach((c, i) => {
      const row = body.insertRow();
      const letter = row.insertCell();
      letter.className = "choice-letter";
      letter.textContent = String.fromCharCode(97 + i) + ")";
      const text = row.insertCell();
      text.className = "choice-text";
      text.textContent = c.content;
      [true, false].forEach((value) => {
        const cell = row.insertCell();
        cell.className = "tf-opt";
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "radio";
        input.name = `q-${q.id}-${c.id}`;
        input.checked = current[c.id] === value;
        input.addEventListener("change", () => {
          const next = Object.assign({}, answers[q.id] || {});
          next[c.id] = value;
          setAnswer(q.id, next);
        });
        label.append(input, value ? "Đúng" : "Sai");
        cell.append(label);
      });
    });
    return table;
  }

  // Phần III: a short numeric answer.
  function renderShortAnswer(q, saved) {
    const wrap = document.createElement("div");
    wrap.className = "short-input";
    const input = document.createElement("input");
    input.type = "text";
    input.inputMode = "decimal";
    input.maxLength = 20;
    input.autocomplete = "off";
    input.placeholder = "Nhập đáp số";
    input.value = (saved && saved[q.id]) || "";
    input.addEventListener("input", () => setAnswer(q.id, input.value.trim() || null));
    const hint = document.createElement("span");
    hint.className = "hint";
    hint.textContent = "Chỉ ghi số, dùng dấu phẩy cho số thập phân (VD: 1,25).";
    wrap.append(input, hint);
    return wrap;
  }

  // "answered" = fully answered, "partial" = some statements of a true/false question left.
  function answerState(questionId) {
    const value = answers[questionId];
    if (value === undefined || value === null || value === "") return "";
    const q = questionsById[questionId];
    if (q && q.type === "TRUE_FALSE") {
      const marked = Object.keys(value).length;
      return marked >= q.choices.length ? "answered" : marked ? "partial" : "";
    }
    return "answered";
  }

  // ------------------------------------------------------------------ rendering

  function renderPaper(data) {
    els.total.textContent = data.questions.length;
    questionCount = data.questions.length;
    data.questions.forEach((q) => (questionsById[q.id] = q));
    const cards = data.questions.map((q) => {
      const card = document.createElement("section");
      card.className = "card question-card";
      card.id = "q-" + q.id;

      const head = document.createElement("div");
      head.className = "q-number";
      head.textContent = "Câu " + q.number;

      // textContent (never innerHTML) so question text cannot inject markup; KaTeX renders afterwards.
      const content = document.createElement("div");
      content.className = "q-content";
      content.textContent = q.content;

      const answerBox =
        q.type === "TRUE_FALSE" ? renderTrueFalse(q, data.answers) :
        q.type === "SHORT_ANSWER" ? renderShortAnswer(q, data.answers) :
        q.type === "ESSAY" ? renderEssay(q, data.answers) :
        renderChoices(q, data.answers);

      const badge = document.createElement("span");
      badge.className = "badge type-badge";
      badge.textContent = TYPE_LABELS[q.type] || "";
      head.append(" ", badge);
      const flagBtn = document.createElement("button");
      flagBtn.type = "button";
      flagBtn.className = "flag-btn";
      const isFlagged = flags.has(String(q.id));
      flagBtn.setAttribute("aria-pressed", isFlagged ? "true" : "false");
      flagBtn.textContent = isFlagged ? "🚩 Đã cắm cờ" : "🚩 Phân vân";
      flagBtn.title = "Đánh dấu câu còn phân vân để xem lại trước khi nộp";
      flagBtn.addEventListener("click", () => toggleFlag(q.id, flagBtn));
      head.append(flagBtn);

      const parts = [head, content];
      if (q.image_url) {
        const img = document.createElement("img");
        img.className = "q-figure";
        img.src = q.image_url;
        img.alt = "Hình vẽ câu " + q.number;
        parts.push(img);
      }
      card.append(...parts, answerBox);
      return card;
    });
    els.questions.replaceChildren(...cards);

    els.palette.replaceChildren(
      ...data.questions.map((q) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = q.number;
        btn.dataset.questionId = q.id;
        btn.addEventListener("click", () => document.getElementById("q-" + q.id).scrollIntoView({ behavior: "smooth", block: "start" }));
        return btn;
      })
    );

    Object.assign(answers, data.answers || {});
    refreshProgress();
    if (window.renderMath) window.renderMath(els.questions);
    else window.addEventListener("load", () => window.renderMath && window.renderMath(els.questions));
  }

  function setAnswer(questionId, value) {
    if (locked) return;
    activeId = String(questionId); // answering a question makes it the one being worked on
    if (value === null) delete answers[questionId];
    else answers[questionId] = value;
    recordChange(questionId, value);
    refreshProgress();
  }

  // Time per question: the card most visible on screen (or the one last answered) gets each second
  // while the tab is visible. Totals are an estimate used for the "slowest questions" review filter.
  function startQuestionTimer() {
    const ratios = new Map();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => ratios.set(e.target.id.slice(2), e.intersectionRatio));
        let best = null;
        let bestRatio = 0;
        ratios.forEach((ratio, id) => {
          if (ratio > bestRatio) [best, bestRatio] = [id, ratio];
        });
        if (best) activeId = best;
      },
      { threshold: [0, 0.25, 0.5, 0.75, 1] }
    );
    document.querySelectorAll(".question-card").forEach((card) => observer.observe(card));
    let ticks = 0;
    setInterval(() => {
      if (locked || document.visibilityState !== "visible" || !activeId) return;
      times[activeId] = (times[activeId] || 0) + 1;
      timesDirty = true;
      if (++ticks % 5 === 0) local.save();
      if (ticks % 30 === 0) syncNow(); // keep the server copy fresh even without answer changes
    }, 1000);
  }

  function refreshProgress() {
    let count = 0;
    els.palette.querySelectorAll("button").forEach((btn) => {
      const state = answerState(btn.dataset.questionId);
      btn.classList.toggle("answered", state === "answered");
      btn.classList.toggle("partial", state === "partial");
      btn.classList.toggle("flagged", flags.has(String(btn.dataset.questionId)));
      if (state === "answered") count++;
    });
    els.answered.textContent = count;
  }

  // ------------------------------------------------------------------ start

  async function init() {
    try {
      const sentAt = performance.now();
      const data = await fetchJson(`/api/attempts/${attemptId}/paper`);
      setDeadline(data.remaining_seconds, sentAt, performance.now());
      // Session recovery: start from what the server saved, then re-apply changes that never reached it.
      const saved = await local.load();
      const restored = Object.assign({}, data.answers || {});
      if (saved && saved.pending) {
        for (const [qid, value] of Object.entries(saved.pending)) {
          if (value === null) delete restored[qid];
          else restored[qid] = value;
          pending[qid] = value;
        }
      }
      data.answers = restored;
      (saved && saved.flagsDirty ? saved.flags : data.flags || []).forEach((id) => flags.add(String(id)));
      for (const source of [data.times || {}, (saved && saved.times) || {}]) {
        for (const [id, sec] of Object.entries(source)) times[id] = Math.max(times[id] || 0, sec);
      }
      if (saved && saved.flagsDirty) flagsDirty = true;
      renderPaper(data);
      const count = Object.keys(restored).length;
      if (count) setSaveStatus("saved", `↺ Đã khôi phục ${count} câu đã làm`);
      if (Object.keys(pending).length || flagsDirty) scheduleSync();
      if (data.status !== "IN_PROGRESS" || data.remaining_seconds <= 0) lock();
    } catch (err) {
      if (err.message === "unauthenticated") return;
      els.error.textContent = "Không tải được đề thi. Hãy kiểm tra kết nối mạng rồi tải lại trang.";
      els.error.hidden = false;
      els.questions.replaceChildren();
      return;
    }
    tick();
    setInterval(tick, 250);
    startQuestionTimer();
    setInterval(resync, RESYNC_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") resync();
    });
    // Answers are auto-saved; only warn if some changes have not reached the server yet.
    window.addEventListener("beforeunload", (e) => {
      if (!locked && !submitting && Object.keys(pending).length) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
  }

  init();
})();
