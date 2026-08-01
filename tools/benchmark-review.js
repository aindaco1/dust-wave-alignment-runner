"use strict";

const MAX_PACKET_BYTES = 8 * 1024 * 1024;
const MAX_AUDIO_BYTES = 1024 * 1024 * 1024;
const DIGEST = /^[a-f0-9]{64}$/u;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u;
const translations = {
  en: {
    language: "Language",
    eyebrow: "Dust Wave private tool",
    title: "Alignment benchmark review",
    lede: "Review exact word boundaries locally. Nothing is uploaded.",
    setupTitle: "Load private evidence",
    setupHelp: "Select the immutable packet, then the matching local audio fixtures.",
    packetLabel: "Review packet",
    audioLabel: "Fixture audio",
    progressLabel: "Saved progress (optional)",
    reviewTitle: "Review boundaries",
    play: "Play word preview",
    startLabel: "Start (milliseconds)",
    endLabel: "End (milliseconds)",
    scorableLabel: "Include this word in scored gold evidence",
    previewLegend: "Does the preview avoid clipping the word?",
    yes: "Yes",
    no: "No",
    previous: "Previous",
    next: "Next",
    markReviewed: "Mark reviewed and continue",
    exportTitle: "Save review evidence",
    exportHelp: "Progress exports are resumable. Final export unlocks only after every decision.",
    exportProgress: "Export progress",
    exportFinal: "Export completed review",
    packetReady: ({ fixtures, words }) => `${fixtures} fixture${fixtures === 1 ? "" : "s"} and ${words} word${words === 1 ? "" : "s"} loaded. Select matching audio.`,
    audioReady: ({ matched, fixtures }) => `${matched} of ${fixtures} exact audio fixtures matched.`,
    progress: ({ reviewed, total }) => `${reviewed} of ${total} word${total === 1 ? "" : "s"} reviewed`,
    fixture: ({ fixture, language }) => `${fixture} · ${language.toUpperCase()}`,
    candidate: ({ start, end }) => `Candidate: ${start}–${end} ms`,
    audioRequired: "Select the exact matching audio before reviewing this word.",
    invalidTiming: "Enter a valid boundary inside the fixture duration.",
    previewRequired: "Choose whether this required preview clips the word.",
    reviewed: "Decision recorded.",
    progressExported: "Progress export created locally.",
    finalExported: "Completed review export created locally.",
    progressImported: ({ count }) => `${count} reviewed decisions restored.`,
    invalidPacket: "The selected review packet is invalid.",
    invalidAudio: "One or more audio files did not match the packet or duration.",
    invalidProgress: "Saved progress does not match this packet.",
    allRequired: "Review every word and load every exact audio fixture before final export."
  },
  es: {
    language: "Idioma",
    eyebrow: "Herramienta privada de Dust Wave",
    title: "Revisión del punto de referencia de alineación",
    lede: "Revisa localmente los límites exactos de cada palabra. No se sube nada.",
    setupTitle: "Cargar evidencia privada",
    setupHelp: "Selecciona el paquete inmutable y luego los archivos de audio locales correspondientes.",
    packetLabel: "Paquete de revisión",
    audioLabel: "Audio de las muestras",
    progressLabel: "Progreso guardado (opcional)",
    reviewTitle: "Revisar límites",
    play: "Reproducir vista previa",
    startLabel: "Inicio (milisegundos)",
    endLabel: "Fin (milisegundos)",
    scorableLabel: "Incluir esta palabra en la evidencia puntuada",
    previewLegend: "¿La vista previa evita recortar la palabra?",
    yes: "Sí",
    no: "No",
    previous: "Anterior",
    next: "Siguiente",
    markReviewed: "Marcar revisada y continuar",
    exportTitle: "Guardar evidencia de revisión",
    exportHelp: "Las exportaciones de progreso se pueden reanudar. La exportación final se activa al completar todas las decisiones.",
    exportProgress: "Exportar progreso",
    exportFinal: "Exportar revisión completa",
    packetReady: ({ fixtures, words }) => `Se cargaron ${fixtures} muestra${fixtures === 1 ? "" : "s"} y ${words} palabra${words === 1 ? "" : "s"}. Selecciona el audio correspondiente.`,
    audioReady: ({ matched, fixtures }) => `${matched} de ${fixtures} archivos de audio exactos coinciden.`,
    progress: ({ reviewed, total }) => `${reviewed} de ${total} palabra${total === 1 ? "" : "s"} revisada${total === 1 ? "" : "s"}`,
    fixture: ({ fixture, language }) => `${fixture} · ${language.toUpperCase()}`,
    candidate: ({ start, end }) => `Candidato: ${start}–${end} ms`,
    audioRequired: "Selecciona el audio exacto antes de revisar esta palabra.",
    invalidTiming: "Introduce límites válidos dentro de la duración de la muestra.",
    previewRequired: "Indica si esta vista previa obligatoria recorta la palabra.",
    reviewed: "Decisión registrada.",
    progressExported: "La exportación de progreso se creó localmente.",
    finalExported: "La revisión completa se creó localmente.",
    progressImported: ({ count }) => `Se restauraron ${count} decisiones revisadas.`,
    invalidPacket: "El paquete de revisión seleccionado no es válido.",
    invalidAudio: "Uno o más archivos de audio no coinciden con el paquete o su duración.",
    invalidProgress: "El progreso guardado no corresponde a este paquete.",
    allRequired: "Revisa cada palabra y carga cada audio exacto antes de la exportación final."
  }
};

const elements = Object.fromEntries([
  "packet-file",
  "audio-files",
  "progress-file",
  "setup-status",
  "review-panel",
  "export-panel",
  "progress-text",
  "progress-meter",
  "fixture-context",
  "word-text",
  "candidate-context",
  "fixture-player",
  "play-preview",
  "word-start",
  "word-end",
  "word-scorable",
  "preview-decision",
  "previous-word",
  "next-word",
  "mark-reviewed",
  "review-status",
  "export-progress",
  "export-final",
  "export-status"
].map((id) => [id, document.getElementById(id)]));

let locale = "en";
let packet = null;
let packetSha256 = "";
let fixtures = [];
let words = [];
let currentIndex = 0;
const decisions = new Map();
const fixtureAudio = new Map();

function copy(key, values = {}) {
  const value = translations[locale][key];
  return typeof value === "function" ? value(values) : value;
}

function setLocale(nextLocale) {
  if (!translations[nextLocale]) return;
  locale = nextLocale;
  document.documentElement.lang = locale;
  document.querySelectorAll("[data-language]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.language === locale));
  });
  document.querySelectorAll("[data-copy]").forEach((node) => {
    node.textContent = copy(node.dataset.copy);
  });
  document.querySelectorAll("[data-copy-aria]").forEach((node) => {
    node.setAttribute("aria-label", copy(node.dataset.copyAria));
  });
  updateProgress();
  if (words.length) renderWord();
}

function setStatus(element, message, state = "") {
  element.textContent = message;
  if (state) element.dataset.state = state;
  else delete element.dataset.state;
}

function revokeAudio() {
  for (const audio of fixtureAudio.values()) URL.revokeObjectURL(audio.url);
  fixtureAudio.clear();
  elements["fixture-player"].removeAttribute("src");
  elements["fixture-player"].load();
}

function resetPacketState() {
  revokeAudio();
  packet = null;
  packetSha256 = "";
  fixtures = [];
  words = [];
  currentIndex = 0;
  decisions.clear();
  elements["audio-files"].disabled = true;
  elements["progress-file"].disabled = true;
  elements["review-panel"].hidden = true;
  elements["export-panel"].hidden = true;
  setStatus(elements["setup-status"], "");
}

function object(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

function validDigest(value) {
  return typeof value === "string" && DIGEST.test(value);
}

function validIdentifier(value) {
  return typeof value === "string" && IDENTIFIER.test(value);
}

function validatePacket(value) {
  if (!object(value) || value.schemaVersion !== "alignment-benchmark-review-packet-v1") {
    throw new TypeError("schema");
  }
  if (!Array.isArray(value.fixtures) || value.fixtures.length < 1 || value.fixtures.length > 64) {
    throw new TypeError("fixtures");
  }
  const seenFixtures = new Set();
  const flattened = [];
  const normalizedFixtures = value.fixtures.map((fixture) => {
    if (!object(fixture) || !validIdentifier(fixture.fixtureId) || seenFixtures.has(fixture.fixtureId)) {
      throw new TypeError("fixture");
    }
    seenFixtures.add(fixture.fixtureId);
    if (!['en', 'es'].includes(fixture.language)
      || !Number.isInteger(fixture.audioDurationMs)
      || fixture.audioDurationMs < 120000
      || fixture.audioDurationMs > 300000
      || !validDigest(fixture.sourceAudioSha256)
      || !Array.isArray(fixture.reviewWords)
      || fixture.reviewWords.length < 1
      || fixture.reviewWords.length > 500) {
      throw new TypeError("fixture evidence");
    }
    const seenWords = new Set();
    const reviewWords = fixture.reviewWords.map((word) => {
      if (!object(word)
        || !validIdentifier(word.wordId)
        || !validIdentifier(word.cueId)
        || seenWords.has(word.wordId)
        || typeof word.text !== "string"
        || !word.text.trim()
        || word.text.length > 500
        || !Number.isInteger(word.candidateStartsAtMs)
        || !Number.isInteger(word.candidateEndsAtMs)
        || word.candidateStartsAtMs < 0
        || word.candidateEndsAtMs <= word.candidateStartsAtMs
        || word.candidateEndsAtMs > fixture.audioDurationMs
        || typeof word.previewReviewRequired !== "boolean") {
        throw new TypeError("word");
      }
      seenWords.add(word.wordId);
      const entry = { fixture, word, key: `${fixture.fixtureId}:${word.wordId}` };
      flattened.push(entry);
      return word;
    });
    return { ...fixture, reviewWords };
  });
  return { packet: value, fixtures: normalizedFixtures, words: flattened };
}

async function digestFile(file) {
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function readJson(file, maximumBytes) {
  if (!file || file.size < 1 || file.size > maximumBytes) throw new TypeError("size");
  return JSON.parse(await file.text());
}

async function loadPacket(file) {
  resetPacketState();
  try {
    const [value, digest] = await Promise.all([
      readJson(file, MAX_PACKET_BYTES),
      digestFile(file)
    ]);
    const validated = validatePacket(value);
    packet = validated.packet;
    packetSha256 = digest;
    fixtures = validated.fixtures;
    words = validated.words;
    for (const entry of words) {
      decisions.set(entry.key, {
        startsAtMs: entry.word.candidateStartsAtMs,
        endsAtMs: entry.word.candidateEndsAtMs,
        scorable: true,
        acceptedWithoutClipping: null,
        reviewed: false
      });
    }
    elements["audio-files"].disabled = false;
    elements["progress-file"].disabled = false;
    elements["review-panel"].hidden = false;
    elements["export-panel"].hidden = false;
    setStatus(elements["setup-status"], copy("packetReady", {
      fixtures: fixtures.length,
      words: words.length
    }), "success");
    currentIndex = 0;
    renderWord();
    updateProgress();
  } catch {
    resetPacketState();
    setStatus(elements["setup-status"], copy("invalidPacket"), "error");
  }
}

function audioDuration(url) {
  return new Promise((resolve, reject) => {
    const audio = new Audio();
    const cleanup = () => {
      audio.removeAttribute("src");
      audio.load();
    };
    audio.addEventListener("loadedmetadata", () => {
      const durationMs = Math.round(audio.duration * 1000);
      cleanup();
      resolve(durationMs);
    }, { once: true });
    audio.addEventListener("error", () => {
      cleanup();
      reject(new TypeError("audio"));
    }, { once: true });
    audio.src = url;
  });
}

async function loadAudioFiles(fileList) {
  if (!packet) return;
  let invalid = false;
  const fixturesByDigest = new Map(fixtures.map((fixture) => [fixture.sourceAudioSha256, fixture]));
  for (const file of [...fileList]) {
    let url = "";
    try {
      if (file.size < 1 || file.size > MAX_AUDIO_BYTES) throw new TypeError("size");
      const digest = await digestFile(file);
      const fixture = fixturesByDigest.get(digest);
      if (!fixture) throw new TypeError("digest");
      url = URL.createObjectURL(file);
      const measuredDurationMs = await audioDuration(url);
      if (Math.abs(measuredDurationMs - fixture.audioDurationMs) > 2000) {
        throw new TypeError("duration");
      }
      const previous = fixtureAudio.get(fixture.fixtureId);
      if (previous) URL.revokeObjectURL(previous.url);
      fixtureAudio.set(fixture.fixtureId, { url, measuredDurationMs });
      url = "";
    } catch {
      invalid = true;
      if (url) URL.revokeObjectURL(url);
    }
  }
  setStatus(elements["setup-status"], copy("audioReady", {
    matched: fixtureAudio.size,
    fixtures: fixtures.length
  }), invalid ? "error" : "success");
  if (invalid) setStatus(elements["review-status"], copy("invalidAudio"), "error");
  renderWord();
  updateProgress();
}

function currentEntry() {
  return words[currentIndex] || null;
}

function previewDecision() {
  const selected = document.querySelector('input[name="preview-accepted"]:checked');
  if (!selected) return null;
  return selected.value === "yes";
}

function captureCurrent({ reviewed = false } = {}) {
  const entry = currentEntry();
  if (!entry) return false;
  const startsAtMs = Number(elements["word-start"].value);
  const endsAtMs = Number(elements["word-end"].value);
  const scorable = elements["word-scorable"].checked;
  if (!Number.isInteger(startsAtMs)
    || !Number.isInteger(endsAtMs)
    || startsAtMs < 0
    || endsAtMs <= startsAtMs
    || endsAtMs > entry.fixture.audioDurationMs) {
    if (reviewed) setStatus(elements["review-status"], copy("invalidTiming"), "error");
    return false;
  }
  let acceptedWithoutClipping = null;
  if (entry.word.previewReviewRequired && scorable) {
    acceptedWithoutClipping = previewDecision();
    if (reviewed && typeof acceptedWithoutClipping !== "boolean") {
      setStatus(elements["review-status"], copy("previewRequired"), "error");
      return false;
    }
  }
  const audio = fixtureAudio.get(entry.fixture.fixtureId);
  if (reviewed && !audio) {
    setStatus(elements["review-status"], copy("audioRequired"), "error");
    return false;
  }
  decisions.set(entry.key, {
    startsAtMs,
    endsAtMs,
    scorable,
    acceptedWithoutClipping,
    reviewed
  });
  return true;
}

function renderWord() {
  const entry = currentEntry();
  if (!entry) return;
  const decision = decisions.get(entry.key);
  elements["fixture-context"].textContent = copy("fixture", {
    fixture: entry.fixture.fixtureId,
    language: entry.fixture.language
  });
  elements["word-text"].textContent = entry.word.text;
  elements["candidate-context"].textContent = copy("candidate", {
    start: entry.word.candidateStartsAtMs,
    end: entry.word.candidateEndsAtMs
  });
  elements["word-start"].value = String(decision.startsAtMs);
  elements["word-start"].max = String(entry.fixture.audioDurationMs - 1);
  elements["word-end"].value = String(decision.endsAtMs);
  elements["word-end"].max = String(entry.fixture.audioDurationMs);
  elements["word-scorable"].checked = decision.scorable;
  elements["preview-decision"].hidden = !entry.word.previewReviewRequired || !decision.scorable;
  document.querySelectorAll('input[name="preview-accepted"]').forEach((radio) => {
    radio.checked = decision.acceptedWithoutClipping === (radio.value === "yes");
  });
  elements["previous-word"].disabled = currentIndex === 0;
  elements["next-word"].disabled = currentIndex === words.length - 1;
  const audio = fixtureAudio.get(entry.fixture.fixtureId);
  elements["play-preview"].disabled = !audio;
  if (audio) {
    if (elements["fixture-player"].src !== audio.url) {
      elements["fixture-player"].src = audio.url;
      elements["fixture-player"].load();
    }
  } else {
    elements["fixture-player"].removeAttribute("src");
    elements["fixture-player"].load();
  }
  setStatus(elements["review-status"], decision.reviewed ? copy("reviewed") : "", decision.reviewed ? "success" : "");
}

function invalidateCurrentReview() {
  const entry = currentEntry();
  if (!entry) return;
  const decision = decisions.get(entry.key);
  if (decision.reviewed) decision.reviewed = false;
  if (!elements["word-scorable"].checked) {
    decision.acceptedWithoutClipping = null;
    elements["preview-decision"].hidden = true;
  } else {
    elements["preview-decision"].hidden = !entry.word.previewReviewRequired;
  }
  setStatus(elements["review-status"], "");
  updateProgress();
}

function navigate(offset) {
  captureCurrent();
  currentIndex = Math.max(0, Math.min(words.length - 1, currentIndex + offset));
  renderWord();
}

function markReviewed() {
  if (!captureCurrent({ reviewed: true })) return;
  setStatus(elements["review-status"], copy("reviewed"), "success");
  const nextUnreviewed = words.findIndex((entry, index) => index > currentIndex && !decisions.get(entry.key).reviewed);
  if (nextUnreviewed >= 0) currentIndex = nextUnreviewed;
  updateProgress();
  renderWord();
}

function updateProgress() {
  const reviewed = [...decisions.values()].filter((decision) => decision.reviewed).length;
  const total = words.length;
  elements["progress-text"].textContent = total ? copy("progress", { reviewed, total }) : "";
  elements["progress-meter"].max = Math.max(1, total);
  elements["progress-meter"].value = reviewed;
  elements["export-progress"].disabled = reviewed === 0;
  elements["export-final"].disabled = !(
    total > 0
    && reviewed === total
    && fixtureAudio.size === fixtures.length
    && [...decisions.values()].every((decision) => decision.reviewed)
  );
}

function playPreview() {
  const entry = currentEntry();
  const audio = entry && fixtureAudio.get(entry.fixture.fixtureId);
  if (!entry || !audio || !captureCurrent()) return;
  const decision = decisions.get(entry.key);
  const player = elements["fixture-player"];
  const stopAt = Math.min(entry.fixture.audioDurationMs, decision.endsAtMs + 350) / 1000;
  player.currentTime = Math.max(0, decision.startsAtMs - 350) / 1000;
  const stop = () => {
    if (player.currentTime >= stopAt) {
      player.pause();
      player.removeEventListener("timeupdate", stop);
    }
  };
  player.addEventListener("timeupdate", stop);
  player.play().catch(() => {
    player.removeEventListener("timeupdate", stop);
    setStatus(elements["review-status"], copy("invalidAudio"), "error");
  });
}

function completionPayload() {
  return {
    schemaVersion: "alignment-benchmark-review-completion-v1",
    packetSha256,
    reviews: words
      .filter((entry) => decisions.get(entry.key).reviewed)
      .map((entry) => {
        const decision = decisions.get(entry.key);
        return {
          fixtureId: entry.fixture.fixtureId,
          wordId: entry.word.wordId,
          startsAtMs: decision.startsAtMs,
          endsAtMs: decision.endsAtMs,
          scorable: decision.scorable,
          acceptedWithoutClipping: decision.acceptedWithoutClipping
        };
      })
  };
}

function downloadCompletion(final) {
  if (!packet) return;
  if (final && elements["export-final"].disabled) {
    setStatus(elements["export-status"], copy("allRequired"), "error");
    return;
  }
  captureCurrent();
  const content = `${JSON.stringify(completionPayload(), null, 2)}\n`;
  const blob = new Blob([content], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `alignment-benchmark-review-${packetSha256.slice(0, 12)}-${final ? "complete" : "progress"}.json`;
  link.click();
  URL.revokeObjectURL(url);
  setStatus(elements["export-status"], copy(final ? "finalExported" : "progressExported"), "success");
}

async function importProgress(file) {
  if (!packet) return;
  try {
    const value = await readJson(file, MAX_PACKET_BYTES);
    if (!object(value)
      || value.schemaVersion !== "alignment-benchmark-review-completion-v1"
      || value.packetSha256 !== packetSha256
      || !Array.isArray(value.reviews)
      || value.reviews.length > words.length) {
      throw new TypeError("progress");
    }
    const expected = new Map(words.map((entry) => [entry.key, entry]));
    const imported = new Set();
    for (const review of value.reviews) {
      if (!object(review)
        || !validIdentifier(review.fixtureId)
        || !validIdentifier(review.wordId)
        || typeof review.scorable !== "boolean") {
        throw new TypeError("review");
      }
      const key = `${review.fixtureId}:${review.wordId}`;
      const entry = expected.get(key);
      if (!entry || imported.has(key)
        || !Number.isInteger(review.startsAtMs)
        || !Number.isInteger(review.endsAtMs)
        || review.startsAtMs < 0
        || review.endsAtMs <= review.startsAtMs
        || review.endsAtMs > entry.fixture.audioDurationMs) {
        throw new TypeError("review evidence");
      }
      if (entry.word.previewReviewRequired && review.scorable) {
        if (typeof review.acceptedWithoutClipping !== "boolean") throw new TypeError("preview");
      } else if (review.acceptedWithoutClipping !== null) {
        throw new TypeError("preview applicability");
      }
      imported.add(key);
      decisions.set(key, {
        startsAtMs: review.startsAtMs,
        endsAtMs: review.endsAtMs,
        scorable: review.scorable,
        acceptedWithoutClipping: review.acceptedWithoutClipping,
        reviewed: true
      });
    }
    setStatus(elements["setup-status"], copy("progressImported", { count: imported.size }), "success");
    const firstUnreviewed = words.findIndex((entry) => !decisions.get(entry.key).reviewed);
    currentIndex = firstUnreviewed >= 0 ? firstUnreviewed : 0;
    renderWord();
    updateProgress();
  } catch {
    setStatus(elements["setup-status"], copy("invalidProgress"), "error");
  }
}

document.querySelectorAll("[data-language]").forEach((button) => {
  button.addEventListener("click", () => setLocale(button.dataset.language));
});
elements["packet-file"].addEventListener("change", (event) => loadPacket(event.target.files[0]));
elements["audio-files"].addEventListener("change", (event) => loadAudioFiles(event.target.files));
elements["progress-file"].addEventListener("change", (event) => importProgress(event.target.files[0]));
elements["previous-word"].addEventListener("click", () => navigate(-1));
elements["next-word"].addEventListener("click", () => navigate(1));
elements["mark-reviewed"].addEventListener("click", markReviewed);
elements["play-preview"].addEventListener("click", playPreview);
elements["export-progress"].addEventListener("click", () => downloadCompletion(false));
elements["export-final"].addEventListener("click", () => downloadCompletion(true));
elements["word-start"].addEventListener("input", invalidateCurrentReview);
elements["word-end"].addEventListener("input", invalidateCurrentReview);
elements["word-scorable"].addEventListener("change", invalidateCurrentReview);
document.querySelectorAll('input[name="preview-accepted"]').forEach((radio) => {
  radio.addEventListener("change", invalidateCurrentReview);
});
window.addEventListener("beforeunload", revokeAudio);

setLocale("en");
updateProgress();
