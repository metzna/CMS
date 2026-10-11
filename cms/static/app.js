"use strict";

const listEl = document.getElementById("post-list");
const formEl = document.getElementById("post-form");
const emptyEl = document.getElementById("empty");
const statusEl = document.getElementById("status");
const titleEl = document.getElementById("title");
const dateEl = document.getElementById("date");
const draftEl = document.getElementById("draft");
const bodyEl = document.getElementById("body");
const previewEl = document.getElementById("preview");
const filenameEl = document.getElementById("filename");
const previewBtn = document.getElementById("toggle-preview");
const deleteBtn = document.getElementById("delete");
const buildStateEl = document.getElementById("build-state");
const saveBtn = document.getElementById("save");
const newBtn = document.getElementById("new-post");
const imageBtn = document.getElementById("image");
const imageInput = document.getElementById("image-file");
const sectionEl = document.getElementById("section");
const themeBtn = document.getElementById("theme");

let current = null;        // file name of the open entry, null = new entry
let section = null;        // id of the selected section
let sections = [];

/* --- Appearance ------------------------------------------------------- */

const THEMES = ["system", "light", "dark"];
const THEME_TEXT = { system: "System", light: "Light", dark: "Dark" };

let themeChoice = "system"; // also applies when localStorage doesn't respond

function readTheme() {
  try {
    const chosen = localStorage.getItem("cms-theme");
    return THEMES.includes(chosen) ? chosen : "system";
  } catch (error) {
    return "system";
  }
}

function setTheme(choice) {
  themeChoice = choice;
  if (choice === "system") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = choice;
  }
  themeBtn.textContent = `Theme: ${THEME_TEXT[choice]}`;
  try {
    if (choice === "system") localStorage.removeItem("cms-theme");
    else localStorage.setItem("cms-theme", choice);
  } catch (error) { /* without storage the choice only lasts for this session */ }
}

/* --- Markdown preview ------------------------------------------------- */

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

// Only relative URLs and http(s)/mailto get through; javascript:, data: and
// the like would run code in the CMS on click. Browsers ignore leading control
// characters, so "\x01javascript:" counts as javascript: -- reject those too.
// Entities are no way around this: the text is escaped before parsing, so
// "&#106;avascript:" stays literal.
const SAFE_SCHEMES = ["http", "https", "mailto"];

function safeUrl(url) {
  if (/[\x00-\x20\x7f]/.test(url)) return null;
  const scheme = url.match(/^([a-z][a-z0-9+.-]*):/i);
  if (!scheme) return url;   // relative: /uploads/..., #anchor, page/
  return SAFE_SCHEMES.includes(scheme[1].toLowerCase()) ? url : null;
}

function inline(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (whole, alt, url) => {
      const safe = safeUrl(url);
      return safe ? `<img src="${safe}" alt="${alt}">` : alt;
    })
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (whole, label, url) => {
      const safe = safeUrl(url);
      return safe ? `<a href="${safe}" rel="noreferrer">${label}</a>` : label;
    });
}

// Deliberately small: covers what actually appears in the posts.
function renderMarkdown(source) {
  const lines = escapeHtml(source).split("\n");
  const out = [];
  let inCode = false;
  let list = null; // "ul" | "ol" | null
  let paragraph = [];

  const closeParagraph = () => {
    if (paragraph.length) {
      out.push("<p>" + inline(paragraph.join(" ")) + "</p>");
      paragraph = [];
    }
  };
  const closeList = () => {
    if (list) {
      out.push(`</${list}>`);
      list = null;
    }
  };

  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      closeParagraph();
      closeList();
      out.push(inCode ? "</code></pre>" : "<pre><code>");
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      out.push(line);
      continue;
    }

    if (!line.trim()) {
      closeParagraph();
      closeList();
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeParagraph();
      closeList();
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }

    if (/^(---|\*\*\*)\s*$/.test(line)) {
      closeParagraph();
      closeList();
      out.push("<hr>");
      continue;
    }

    const quote = line.match(/^&gt;\s?(.*)$/);
    if (quote) {
      closeParagraph();
      closeList();
      out.push(`<blockquote>${inline(quote[1])}</blockquote>`);
      continue;
    }

    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+\.\s+(.*)$/);
    if (bullet || numbered) {
      closeParagraph();
      const kind = bullet ? "ul" : "ol";
      if (list !== kind) {
        closeList();
        out.push(`<${kind}>`);
        list = kind;
      }
      out.push(`<li>${inline((bullet || numbered)[1])}</li>`);
      continue;
    }

    closeList();
    paragraph.push(line.trim());
  }

  closeParagraph();
  closeList();
  if (inCode) out.push("</code></pre>");
  return out.join("\n");
}

function updatePreview() {
  if (!previewEl.hidden) previewEl.innerHTML = renderMarkdown(bodyEl.value);
}

/* --- Lock while writing ----------------------------------------------- */

// Saving and deleting take as long as the Hugo build. Nothing else may start
// during that time: a double click on a new entry would otherwise create it
// twice (the server appends a number on name clashes), and switching sections
// would pull the rug out from under the operation.
let busy = false;

function setBusy(on) {
  busy = on;
  for (const el of [saveBtn, deleteBtn, newBtn, imageBtn, sectionEl]) el.disabled = on;
  for (const btn of listEl.querySelectorAll("button")) btn.disabled = on;
}

/* --- Build state ------------------------------------------------------ */

// "" | "running" | "done" | "failed". The check mark disappears by itself,
// the cross stays -- the reason is shown in the status line.
let buildStateTimer = null;

function setBuildState(state) {
  clearTimeout(buildStateTimer);
  buildStateEl.className = state ? `build-state ${state}` : "build-state";
  if (state === "done") {
    buildStateTimer = setTimeout(() => { buildStateEl.className = "build-state"; }, 2500);
  }
}

// Responses of the writing routes carry build and git. The text is written
// in any case -- everything after that can fail separately, listed here in
// order of urgency. Returns whether everything went smoothly.
function reportResult(response, successText, prefix) {
  const build = response.build;
  const git = response.git;

  if (build && !build.ok) {
    showStatus(`${prefix}, but the site was not rebuilt: ${build.message}`, true);
    return false;
  }
  if (git && !git.ok) {
    showStatus(`${prefix}, but not recorded in the version history: `
               + git.message, true);
    return false;
  }
  if (git && git.push && !git.push.ok) {
    // The text is here and versioned, only the off-site backup is stuck.
    showStatus(`${successText} — the backup is stuck: ${git.push.message}`, true);
    return false;
  }
  showStatus(successText);
  return true;
}

/* --- Data ------------------------------------------------------------- */

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Error ${response.status}`);
  return data;
}

function showStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

function entriesPath(file) {
  const base = `/api/sections/${encodeURIComponent(section)}/entries`;
  return file ? `${base}/${encodeURIComponent(file)}` : base;
}

async function loadSiteTitle() {
  try {
    const site = await api("/api/site");
    if (site.title) document.title = site.title;
  } catch (error) { /* nice to have: the UI keeps working without the title */ }
}

async function loadSections() {
  try {
    sections = await api("/api/sections");
    sectionEl.replaceChildren(...sections.map((s) => {
      const option = document.createElement("option");
      option.value = s.id;
      option.textContent = s.missing ? `${s.name} (folder missing)` : `${s.name} (${s.count})`;
      option.disabled = Boolean(s.missing);
      return option;
    }));
    const first = sections.find((s) => !s.missing);
    if (!first) {
      showStatus("No section available – create the folders in website/content/.", true);
      return;
    }
    section = first.id;
    sectionEl.value = section;
    await loadList();
  } catch (error) {
    showStatus(error.message, true);
  }
}

async function loadList() {
  try {
    const entries = await api(entriesPath());
    listEl.replaceChildren(...entries.map(buildListItem));
    markSelection();
  } catch (error) {
    showStatus(error.message, true);
  }
}

function switchSection() {
  section = sectionEl.value;
  current = null;
  formEl.hidden = true;
  emptyEl.hidden = false;
  showStatus("");
  loadList();
}

function buildListItem(post) {
  const li = document.createElement("li");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.dataset.file = post.file;
  btn.append(post.title);

  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = post.date || "no date";
  if (post.error) {
    // The file is in the folder but can't be read.
    const flag = document.createElement("span");
    flag.className = "flag";
    flag.textContent = " · unreadable";
    flag.title = post.error;
    meta.append(flag);
  }
  if (post.draft) {
    const flag = document.createElement("span");
    flag.className = "flag";
    flag.textContent = " · Draft";
    meta.append(flag);
  }
  btn.append(meta);
  btn.disabled = busy;
  btn.addEventListener("click", () => openEntry(post.file));
  li.append(btn);
  return li;
}

function markSelection() {
  for (const btn of listEl.querySelectorAll("button")) {
    btn.setAttribute("aria-current", String(btn.dataset.file === current));
  }
}

function showForm() {
  formEl.hidden = false;
  emptyEl.hidden = true;
  deleteBtn.hidden = !current; // an unsaved draft has nothing to delete
}

async function openEntry(file) {
  try {
    const post = await api(entriesPath(file));
    current = post.file;
    titleEl.value = post.title;
    dateEl.value = post.date;
    draftEl.checked = post.draft;
    bodyEl.value = post.body;
    filenameEl.textContent = post.file;
    showForm();
    markSelection();
    updatePreview();
    showStatus("");
    titleEl.focus();
  } catch (error) {
    showStatus(error.message, true);
  }
}

function newEntry() {
  current = null;
  titleEl.value = "";
  dateEl.value = new Date().toISOString().slice(0, 10);
  draftEl.checked = false;
  bodyEl.value = "";
  filenameEl.textContent = "new entry – file name follows from the title";
  showForm();
  markSelection();
  updatePreview();
  showStatus("");
  titleEl.focus();
}

async function saveEntry(event) {
  event.preventDefault();
  if (busy) return;   // Ctrl+S bypasses the disabled button
  const data = {
    title: titleEl.value,
    date: dateEl.value,
    draft: draftEl.checked,
    body: bodyEl.value,
  };
  const options = {
    method: current ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  };
  const path = entriesPath(current);

  setBusy(true);
  setBuildState("running");
  try {
    const post = await api(path, options);
    const wasNew = !current;
    current = post.file;
    filenameEl.textContent = post.file;
    deleteBtn.hidden = false;
    await loadList();
    if (wasNew) await refreshCounts();
    setBuildState(reportResult(post, `Saved: ${post.file}`, "Saved") ? "done" : "failed");
  } catch (error) {
    setBuildState("");
    showStatus(error.message, true);
  } finally {
    setBusy(false);   // usable again even after an error
  }
}

async function deleteEntry() {
  if (!current || busy) return;
  const file = current;
  const question = `Delete “${titleEl.value.trim() || file}”?\n\n`
    + `The file ${file} will be removed — this cannot be undone.`;
  if (!confirm(question)) return;

  setBusy(true);
  setBuildState("running");
  try {
    const response = await api(entriesPath(file), { method: "DELETE" });
    current = null;
    formEl.hidden = true;
    emptyEl.hidden = false;
    await loadList();
    await refreshCounts();
    // The form is gone and the icon with it -- the message carries the result.
    setBuildState("");
    reportResult(response, `Deleted: ${file}`, "Deleted");
  } catch (error) {
    setBuildState("");
    showStatus(error.message, true);
  } finally {
    setBusy(false);
    deleteBtn.hidden = !current;   // after deleting there is nothing left
  }
}

/* --- Images ----------------------------------------------------------- */

function insertImage(url) {
  const description = "Image description";
  const markup = `![${description}](${url})`;
  const start = bodyEl.selectionStart;
  const end = bodyEl.selectionEnd;
  bodyEl.value = bodyEl.value.slice(0, start) + markup + bodyEl.value.slice(end);
  // Select the placeholder right away so it can be typed over.
  bodyEl.focus();
  bodyEl.setSelectionRange(start + 2, start + 2 + description.length);
  updatePreview();
}

async function uploadImage(file) {
  if (busy || !file) return;
  if (!file.type.startsWith("image/")) {
    showStatus("That is not an image file.", true);
    return;
  }
  setBusy(true);
  setBuildState("running");
  try {
    const response = await api("/api/uploads", {
      method: "POST",
      headers: {
        "Content-Type": file.type,
        "X-Filename": encodeURIComponent(file.name),
      },
      body: file,
    });
    insertImage(response.url);
    const kb = Math.round(response.bytes / 1024);
    const text = `Image inserted: ${response.width}×${response.height}, ${kb} KB`;
    setBuildState(reportResult(response, text, "Image saved") ? "done" : "failed");
  } catch (error) {
    setBuildState("");
    showStatus(error.message, true);
  } finally {
    setBusy(false);
  }
}

/* --- Events ----------------------------------------------------------- */

async function refreshCounts() {
  const selected = sectionEl.value;
  sections = await api("/api/sections");
  for (const option of sectionEl.options) {
    const match = sections.find((s) => s.id === option.value);
    if (match && !match.missing) option.textContent = `${match.name} (${match.count})`;
  }
  sectionEl.value = selected;
}

themeBtn.addEventListener("click", () => {
  const next = THEMES[(THEMES.indexOf(themeChoice) + 1) % THEMES.length];
  setTheme(next);
});

deleteBtn.addEventListener("click", deleteEntry);
sectionEl.addEventListener("change", switchSection);
newBtn.addEventListener("click", newEntry);
formEl.addEventListener("submit", saveEntry);
bodyEl.addEventListener("input", updatePreview);

imageBtn.addEventListener("click", () => imageInput.click());
imageInput.addEventListener("change", () => {
  uploadImage(imageInput.files[0]);
  imageInput.value = "";   // otherwise the same file won't trigger a second time
});

// Dragging an image into the text field works too.
bodyEl.addEventListener("dragover", (event) => {
  event.preventDefault();
  bodyEl.classList.add("drop-target");
});
bodyEl.addEventListener("dragleave", () => bodyEl.classList.remove("drop-target"));
bodyEl.addEventListener("drop", (event) => {
  event.preventDefault();
  bodyEl.classList.remove("drop-target");
  uploadImage(event.dataTransfer.files[0]);
});

previewBtn.addEventListener("click", () => {
  previewEl.hidden = !previewEl.hidden;
  previewBtn.textContent = previewEl.hidden ? "Preview" : "Hide preview";
  updatePreview();
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "s") {
    event.preventDefault();
    if (!formEl.hidden) formEl.requestSubmit();
  }
});

setTheme(readTheme());
loadSiteTitle();
loadSections();
