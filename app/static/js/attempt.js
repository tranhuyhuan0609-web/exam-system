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
  };

  const RESYNC_MS = 30_000;
  const answers = {}; // { questionId: choiceId }
  let deadline = null; // performance.now() value at which time runs out
  let locked = false;
  let submitting = false;
  let questionCount = 0;
  const questionsById = {};

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
          body: JSON.stringify({ answers }),
          cache: "no-store",
        });
        if (res.status === 401) {
          window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
          return;
        }
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
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

  els.submit.addEventListener("click", () => {
    const left = Object.keys(questionsById).filter((id) => answerState(id) !== "answered").length;
    const message = left > 0 ? `Bạn còn ${left} câu chưa chọn đáp án. Vẫn nộp bài?` : "Nộp bài và xem kết quả?";
    if (confirm(message)) submitAnswers();
  });


  // ------------------------------------------------------------------ answer widgets (one per question type)

  const TYPE_LABELS = { MULTIPLE_CHOICE: "Trắc nghiệm", TRUE_FALSE: "Đúng/Sai", SHORT_ANSWER: "Trả lời ngắn" };

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
        renderChoices(q, data.answers);

      const badge = document.createElement("span");
      badge.className = "badge type-badge";
      badge.textContent = TYPE_LABELS[q.type] || "";
      head.append(" ", badge);

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
    if (value === null) delete answers[questionId];
    else answers[questionId] = value;
    refreshProgress();
  }

  function refreshProgress() {
    let count = 0;
    els.palette.querySelectorAll("button").forEach((btn) => {
      const state = answerState(btn.dataset.questionId);
      btn.classList.toggle("answered", state === "answered");
      btn.classList.toggle("partial", state === "partial");
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
      renderPaper(data);
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
    setInterval(resync, RESYNC_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") resync();
    });
    // Answers are only sent on submit until phase 2 auto-save, so warn before leaving.
    window.addEventListener("beforeunload", (e) => {
      if (!locked && !submitting && Object.keys(answers).length) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
  }

  init();
})();
