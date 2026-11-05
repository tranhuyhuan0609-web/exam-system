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
  };

  const RESYNC_MS = 30_000;
  const answers = {}; // { questionId: choiceId }
  let deadline = null; // performance.now() value at which time runs out
  let locked = false;

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

  function lock() {
    if (locked) return;
    locked = true;
    els.timer.textContent = "00:00";
    els.timeUp.hidden = false;
    root.classList.add("locked");
    root.querySelectorAll("input[type=radio]").forEach((input) => (input.disabled = true));
  }

  // ------------------------------------------------------------------ rendering

  function renderPaper(data) {
    els.total.textContent = data.questions.length;
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

      const list = document.createElement("div");
      list.className = "choice-list";
      q.choices.forEach((c, i) => {
        const label = document.createElement("label");
        label.className = "choice";
        const input = document.createElement("input");
        input.type = "radio";
        input.name = "q-" + q.id;
        input.value = c.id;
        input.checked = data.answers && data.answers[q.id] === c.id;
        input.addEventListener("change", () => choose(q.id, c.id));
        const letter = document.createElement("span");
        letter.className = "choice-letter";
        letter.textContent = String.fromCharCode(65 + i);
        const text = document.createElement("span");
        text.className = "choice-text";
        text.textContent = c.content;
        label.append(input, letter, text);
        list.append(label);
      });

      card.append(head, content, list);
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

  function choose(questionId, choiceId) {
    if (locked) return;
    answers[questionId] = choiceId;
    refreshProgress();
  }

  function refreshProgress() {
    let count = 0;
    els.palette.querySelectorAll("button").forEach((btn) => {
      const done = Boolean(answers[btn.dataset.questionId]);
      btn.classList.toggle("answered", done);
      if (done) count++;
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
    // Answers are not saved to the server yet (phase 2), so warn before leaving.
    window.addEventListener("beforeunload", (e) => {
      if (!locked && Object.keys(answers).length) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
  }

  init();
})();
