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
const bauEl = document.getElementById("bau");
const saveBtn = document.getElementById("save");
const neuBtn = document.getElementById("new-post");
const sectionEl = document.getElementById("section");
const themeBtn = document.getElementById("theme");

let aktuell = null;        // Dateiname des offenen Eintrags, null = neuer Eintrag
let sektion = null;        // id der gewaehlten Sektion
let sektionen = [];

/* --- Darstellung ------------------------------------------------------ */

const THEMES = ["system", "light", "dark"];
const THEME_TEXT = { system: "System", light: "Hell", dark: "Dunkel" };

let themeWahl = "system"; // gilt auch dann, wenn localStorage nicht antwortet

function themeLesen() {
  try {
    const gewaehlt = localStorage.getItem("cms-theme");
    return THEMES.includes(gewaehlt) ? gewaehlt : "system";
  } catch (fehler) {
    return "system";
  }
}

function themeSetzen(wahl) {
  themeWahl = wahl;
  if (wahl === "system") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = wahl;
  }
  themeBtn.textContent = `Darstellung: ${THEME_TEXT[wahl]}`;
  try {
    if (wahl === "system") localStorage.removeItem("cms-theme");
    else localStorage.setItem("cms-theme", wahl);
  } catch (fehler) { /* ohne Speicher gilt die Wahl nur fuer diese Sitzung */ }
}

/* --- Markdown-Vorschau ------------------------------------------------ */

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

function inline(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" rel="noreferrer">$1</a>');
}

// Bewusst klein gehalten: deckt ab, was in den Beitraegen vorkommt.
function renderMarkdown(quelle) {
  const zeilen = escapeHtml(quelle).split("\n");
  const out = [];
  let inCode = false;
  let liste = null; // "ul" | "ol" | null
  let absatz = [];

  const absatzSchliessen = () => {
    if (absatz.length) {
      out.push("<p>" + inline(absatz.join(" ")) + "</p>");
      absatz = [];
    }
  };
  const listeSchliessen = () => {
    if (liste) {
      out.push(`</${liste}>`);
      liste = null;
    }
  };

  for (const zeile of zeilen) {
    if (/^\s*```/.test(zeile)) {
      absatzSchliessen();
      listeSchliessen();
      out.push(inCode ? "</code></pre>" : "<pre><code>");
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      out.push(zeile);
      continue;
    }

    if (!zeile.trim()) {
      absatzSchliessen();
      listeSchliessen();
      continue;
    }

    const ueberschrift = zeile.match(/^(#{1,6})\s+(.*)$/);
    if (ueberschrift) {
      absatzSchliessen();
      listeSchliessen();
      const stufe = ueberschrift[1].length;
      out.push(`<h${stufe}>${inline(ueberschrift[2])}</h${stufe}>`);
      continue;
    }

    if (/^(---|\*\*\*)\s*$/.test(zeile)) {
      absatzSchliessen();
      listeSchliessen();
      out.push("<hr>");
      continue;
    }

    const zitat = zeile.match(/^&gt;\s?(.*)$/);
    if (zitat) {
      absatzSchliessen();
      listeSchliessen();
      out.push(`<blockquote>${inline(zitat[1])}</blockquote>`);
      continue;
    }

    const punkt = zeile.match(/^\s*[-*+]\s+(.*)$/);
    const nummer = zeile.match(/^\s*\d+\.\s+(.*)$/);
    if (punkt || nummer) {
      absatzSchliessen();
      const art = punkt ? "ul" : "ol";
      if (liste !== art) {
        listeSchliessen();
        out.push(`<${art}>`);
        liste = art;
      }
      out.push(`<li>${inline((punkt || nummer)[1])}</li>`);
      continue;
    }

    listeSchliessen();
    absatz.push(zeile.trim());
  }

  absatzSchliessen();
  listeSchliessen();
  if (inCode) out.push("</code></pre>");
  return out.join("\n");
}

function vorschauAktualisieren() {
  if (!previewEl.hidden) previewEl.innerHTML = renderMarkdown(bodyEl.value);
}

/* --- Sperre waehrend des Schreibens ----------------------------------- */

// Speichern und Loeschen dauern so lange wie der Hugo-Build. In der Zeit
// darf nichts zweites losgehen: ein Doppelklick auf einen neuen Eintrag
// legte ihn sonst zweimal an (der Server haengt bei Namensgleichheit eine
// Nummer an), und ein Sektionswechsel zoege dem Vorgang den Boden weg.
let amArbeiten = false;

function sperren(ja) {
  amArbeiten = ja;
  for (const el of [saveBtn, deleteBtn, neuBtn, sectionEl]) el.disabled = ja;
  for (const btn of listEl.querySelectorAll("button")) btn.disabled = ja;
}

/* --- Baustand --------------------------------------------------------- */

// "" | "laeuft" | "fertig" | "fehler". Der Haken verschwindet von allein,
// das Kreuz bleibt stehen -- dazu steht der Grund in der Statuszeile.
let bauTimer = null;

function bauStand(zustand) {
  clearTimeout(bauTimer);
  bauEl.className = zustand ? `bau ${zustand}` : "bau";
  if (zustand === "fertig") {
    bauTimer = setTimeout(() => { bauEl.className = "bau"; }, 2500);
  }
}

// Antworten der schreibenden Routen tragen ein build-Feld. Gibt zurueck,
// ob die Seite neu erzeugt wurde -- geschrieben wurde sie in jedem Fall.
function bauMelden(antwort, erfolgstext, vorspann) {
  const bau = antwort.build;
  if (bau && !bau.ok) {
    melde(`${vorspann}, aber die Seite wurde nicht neu erzeugt: ${bau.meldung}`, true);
    return false;
  }
  melde(erfolgstext);
  return true;
}

/* --- Daten ------------------------------------------------------------ */

async function api(pfad, optionen) {
  const antwort = await fetch(pfad, optionen);
  const daten = await antwort.json().catch(() => ({}));
  if (!antwort.ok) throw new Error(daten.error || `Fehler ${antwort.status}`);
  return daten;
}

function melde(text, istFehler = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", istFehler);
}

function entriesPfad(datei) {
  const basis = `/api/sections/${encodeURIComponent(sektion)}/entries`;
  return datei ? `${basis}/${encodeURIComponent(datei)}` : basis;
}

async function seitentitelSetzen() {
  try {
    const seite = await api("/api/site");
    if (seite.title) document.title = seite.title;
  } catch (fehler) { /* Beiwerk: ohne Titel laeuft die Oberflaeche weiter */ }
}

async function sektionenLaden() {
  try {
    sektionen = await api("/api/sections");
    sectionEl.replaceChildren(...sektionen.map((s) => {
      const option = document.createElement("option");
      option.value = s.id;
      option.textContent = s.missing ? `${s.name} (Ordner fehlt)` : `${s.name} (${s.count})`;
      option.disabled = Boolean(s.missing);
      return option;
    }));
    const erste = sektionen.find((s) => !s.missing);
    if (!erste) {
      melde("Keine Sektion verfügbar – Ordner in website/content/ anlegen.", true);
      return;
    }
    sektion = erste.id;
    sectionEl.value = sektion;
    await listeLaden();
  } catch (fehler) {
    melde(fehler.message, true);
  }
}

async function listeLaden() {
  try {
    const eintraege = await api(entriesPfad());
    listEl.replaceChildren(...eintraege.map(eintragBauen));
    markiereAuswahl();
  } catch (fehler) {
    melde(fehler.message, true);
  }
}

function sektionWechseln() {
  sektion = sectionEl.value;
  aktuell = null;
  formEl.hidden = true;
  emptyEl.hidden = false;
  melde("");
  listeLaden();
}

function eintragBauen(post) {
  const li = document.createElement("li");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.dataset.file = post.file;
  btn.append(post.title);

  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = post.date || "ohne Datum";
  if (post.fehler) {
    // Datei liegt im Ordner, laesst sich aber nicht lesen.
    const flag = document.createElement("span");
    flag.className = "flag";
    flag.textContent = " \u00b7 unlesbar";
    flag.title = post.fehler;
    meta.append(flag);
  }
  if (post.draft) {
    const flag = document.createElement("span");
    flag.className = "flag";
    flag.textContent = " · Entwurf";
    meta.append(flag);
  }
  btn.append(meta);
  btn.disabled = amArbeiten;
  btn.addEventListener("click", () => beitragOeffnen(post.file));
  li.append(btn);
  return li;
}

function markiereAuswahl() {
  for (const btn of listEl.querySelectorAll("button")) {
    btn.setAttribute("aria-current", String(btn.dataset.file === aktuell));
  }
}

function formularZeigen() {
  formEl.hidden = false;
  emptyEl.hidden = true;
  deleteBtn.hidden = !aktuell; // ein ungespeicherter Entwurf hat nichts zu loeschen
}

async function beitragOeffnen(datei) {
  try {
    const post = await api(entriesPfad(datei));
    aktuell = post.file;
    titleEl.value = post.title;
    dateEl.value = post.date;
    draftEl.checked = post.draft;
    bodyEl.value = post.body;
    filenameEl.textContent = post.file;
    formularZeigen();
    markiereAuswahl();
    vorschauAktualisieren();
    melde("");
    titleEl.focus();
  } catch (fehler) {
    melde(fehler.message, true);
  }
}

function neuerBeitrag() {
  aktuell = null;
  titleEl.value = "";
  dateEl.value = new Date().toISOString().slice(0, 10);
  draftEl.checked = false;
  bodyEl.value = "";
  filenameEl.textContent = "neuer Eintrag – Dateiname folgt aus dem Titel";
  formularZeigen();
  markiereAuswahl();
  vorschauAktualisieren();
  melde("");
  titleEl.focus();
}

async function speichern(event) {
  event.preventDefault();
  if (amArbeiten) return;   // Strg+S geht am gesperrten Knopf vorbei
  const daten = {
    title: titleEl.value,
    date: dateEl.value,
    draft: draftEl.checked,
    body: bodyEl.value,
  };
  const optionen = {
    method: aktuell ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(daten),
  };
  const pfad = entriesPfad(aktuell);

  sperren(true);
  bauStand("laeuft");
  try {
    const post = await api(pfad, optionen);
    const warNeu = !aktuell;
    aktuell = post.file;
    filenameEl.textContent = post.file;
    deleteBtn.hidden = false;
    await listeLaden();
    if (warNeu) await zaehlerAuffrischen();
    bauStand(bauMelden(post, `Gespeichert: ${post.file}`, "Gespeichert") ? "fertig" : "fehler");
  } catch (fehler) {
    bauStand("");
    melde(fehler.message, true);
  } finally {
    sperren(false);   // auch nach einem Fehler wieder bedienbar
  }
}

async function loeschen() {
  if (!aktuell || amArbeiten) return;
  const datei = aktuell;
  const frage = `\u201e${titleEl.value.trim() || datei}\u201c l\u00f6schen?\n\n`
    + `Die Datei ${datei} wird entfernt \u2014 hier gibt es kein Zur\u00fcck.`;
  if (!confirm(frage)) return;

  sperren(true);
  bauStand("laeuft");
  try {
    const antwort = await api(entriesPfad(datei), { method: "DELETE" });
    aktuell = null;
    formEl.hidden = true;
    emptyEl.hidden = false;
    await listeLaden();
    await zaehlerAuffrischen();
    // Das Formular ist weg, mit ihm das Symbol -- die Meldung traegt es.
    bauStand("");
    bauMelden(antwort, `Gel\u00f6scht: ${datei}`, "Gel\u00f6scht");
  } catch (fehler) {
    bauStand("");
    melde(fehler.message, true);
  } finally {
    sperren(false);
    deleteBtn.hidden = !aktuell;   // nach dem Loeschen gibt es nichts mehr
  }
}

/* --- Ereignisse ------------------------------------------------------- */

async function zaehlerAuffrischen() {
  const gewaehlt = sectionEl.value;
  sektionen = await api("/api/sections");
  for (const option of sectionEl.options) {
    const treffer = sektionen.find((s) => s.id === option.value);
    if (treffer && !treffer.missing) option.textContent = `${treffer.name} (${treffer.count})`;
  }
  sectionEl.value = gewaehlt;
}

themeBtn.addEventListener("click", () => {
  const naechste = THEMES[(THEMES.indexOf(themeWahl) + 1) % THEMES.length];
  themeSetzen(naechste);
});

deleteBtn.addEventListener("click", loeschen);
sectionEl.addEventListener("change", sektionWechseln);
neuBtn.addEventListener("click", neuerBeitrag);
formEl.addEventListener("submit", speichern);
bodyEl.addEventListener("input", vorschauAktualisieren);

previewBtn.addEventListener("click", () => {
  previewEl.hidden = !previewEl.hidden;
  previewBtn.textContent = previewEl.hidden ? "Vorschau" : "Vorschau aus";
  vorschauAktualisieren();
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "s") {
    event.preventDefault();
    if (!formEl.hidden) formEl.requestSubmit();
  }
});

themeSetzen(themeLesen());
seitentitelSetzen();
sektionenLaden();
