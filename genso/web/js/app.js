"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const RESOLUTIONS = {
  "16:9": { draft: [640, 352], standard: [864, 480], high: [1344, 768] },
  "9:16": { draft: [352, 640], standard: [480, 864], high: [768, 1344] },
  "1:1": { draft: [480, 480], standard: [640, 640], high: [768, 768] },
  "3:4": { draft: [416, 544], standard: [544, 736], high: [768, 1024] },
  "4:3": { draft: [544, 416], standard: [736, 544], high: [1024, 768] },
};

const MODE_COPY = {
  t2va: {
    eyebrow: "TEXT TO VIDEO + AUDIO",
    title: "文章から映像と音を描く",
    hint: "英語で、シーン全体→ショット時系列→カメラ→音声の順に書くとよく効きます。音を指定しないと動画内容から推測された音が付きます。",
  },
  fl2va: {
    eyebrow: "FRAME TO VIDEO + AUDIO",
    title: "一枚絵に時間を与える",
    hint: "開始フレームを保ちながら、動き・カメラ・台詞・音を生成します。終了フレームを足すと FL2V、同じ画像ならループ動画になります。",
  },
  ref2va: {
    eyebrow: "REFERENCE TO VIDEO + AUDIO",
    title: "姿・動き・声を参照して作る",
    hint: "画像は人物の姿、動画は動き、音声は声色の参照として使えます。プロンプト内の <Picture 1> などの宣言が重要です。",
  },
  mannequin: {
    eyebrow: "MANNEQUIN RETARGETING",
    title: "モーションをキャラクターへ移す",
    hint: "マネキン動画の動きとカメラを、設定画のキャラクターで描き直す専用プリセットです。",
  },
};

const TEMPLATE_DESCRIPTIONS = {
  t2va_storyboard: "シーン、3つのショット、カメラ、音を順番に整理します。秒数は現在の尺から自動提案します。",
  i2v_dialogue: "開始画像の見た目、動き、日本語の台詞、音を一画面で指定します。台詞は翻訳されません。",
  ref2va_declaration: "追加済みの画像・動画・音声から参照タグを自動構成します。",
  ref2va_subtitle: "台詞と表示区間をまとめて指定し、日本語字幕ブロックを生成します。",
  ref2va_lipsync: "日本語音声に合わせた口・顎・表情の同期指示を挿入します。",
  mannequin_chibi: "追加済みの参照画像タグを使い、ちび体型のモーション転写指示を生成します。",
};

const TEMPLATE_FIELD_META = {
  scene_style: { label: "画風・映像表現", group: "シーン全体", rows: 2, placeholder: "例：手描きセルアニメ、柔らかい朝の光" },
  scene_summary: { label: "シーンの概要", group: "シーン全体", rows: 3, placeholder: "誰が、どこで、何をしているか" },
  t1: { label: "ショット1 終了秒", group: "ショット時系列", type: "number", step: "0.1", min: "0" },
  t2: { label: "ショット2 終了秒", group: "ショット時系列", type: "number", step: "0.1", min: "0" },
  t3: { label: "ショット3 終了秒", group: "ショット時系列", type: "number", step: "0.1", min: "0" },
  shot1: { label: "ショット1", group: "ショット時系列", rows: 2, placeholder: "最初の動き" },
  shot2: { label: "ショット2", group: "ショット時系列", rows: 2, placeholder: "次の動き" },
  shot3: { label: "ショット3", group: "ショット時系列", rows: 2, placeholder: "最後の動き" },
  camera_notes: { label: "カメラ", group: "カメラと音", rows: 2, placeholder: "例：正面固定、ゆっくり寄る" },
  audio_notes: { label: "音・台詞・環境音", group: "カメラと音", rows: 2, placeholder: "例：静かな室内音、BGMなし" },
  style_and_transcription_of_input_image: { label: "開始画像の見た目", group: "画像と動き", rows: 3, placeholder: "人物、服装、背景、画風を記述" },
  motion_description: { label: "起こす動き", group: "画像と動き", rows: 3, placeholder: "時系列に沿って動きを記述" },
  character: { label: "話者", group: "台詞", type: "text", placeholder: "例：character / Shirona" },
  serifu: { label: "日本語の台詞", group: "台詞", rows: 2, placeholder: "翻訳せず、そのまま発話させる文章" },
  soundscape: { label: "環境音・効果音", group: "音", rows: 2, placeholder: "例：quiet room tone only" },
  bgm_or_NA: { label: "BGM", group: "音", type: "text", placeholder: "不要なら NA" },
  subject_desc: { label: "Subject 1 の説明", group: "参照する人物", type: "text", placeholder: "例：main character / Shirona" },
  start: { label: "字幕の開始秒", group: "表示区間", type: "number", step: "0.1", min: "0" },
  end: { label: "字幕の終了秒", group: "表示区間", type: "number", step: "0.1", min: "0" },
  "<Audio 1> is the voice-timbre reference for <Subject 1> (S1).": {
    label: "音声を声色参照として使う", group: "参照の適用", type: "checkbox",
    help: "音声スロット、または音声参照ONの動画がある場合に有効です。",
  },
  "Use <Picture 1> as the first frame, exactly as it is.": {
    label: "Picture 1 を開始フレームに固定", group: "参照の適用", type: "checkbox",
    help: "最初の画面を参照画像と完全に合わせたい場合に使います。",
  },
  "Apply the motion from <Video 1>.": {
    label: "Video 1 の動きを適用", group: "参照の適用", type: "checkbox",
    help: "参照動画のモーションを使います。",
  },
};

const STAGE_NAMES = {
  "1": "モデル読込", "2": "LoRA適用", "3": "スケジュール設定",
  "4": "テキストエンコーダ読込", "5": "VAE読込", "6": "音声VAE読込",
  "7": "プロンプト解釈", "8": "サンプラー選択", "9": "スケジューラ",
  "10": "ノイズ生成", "11": "ガイダンス", "12": "サンプリング",
  "13": "映像・音声の分離", "14": "映像デコード", "15": "音声デコード",
  "16": "動画の合成", "17": "保存", "20": "参照画像読込",
  "21": "先頭フレーム読込", "22": "末尾フレーム読込",
  "23": "参照画像読込", "24": "参照画像読込", "25": "参照画像読込",
  "26": "参照画像読込", "27": "参照画像読込", "28": "参照画像読込",
  "30": "LoRA適用", "31": "LoRA適用", "32": "LoRA適用", "33": "LoRA適用",
  "40": "参照動画読込", "41": "参照動画展開", "42": "参照動画読込",
  "43": "参照動画展開", "44": "参照動画読込", "45": "参照動画展開",
  "50": "参照音声読込", "51": "参照音声読込", "52": "参照音声読込",
  "60": "参照画像読込", "61": "参照画像読込",
};

const SUBTITLE_STYLE = `All subtitles use a clean bold Japanese sans-serif typeface. The text is
solid white with a clearly defined medium-thick black outline, no background
box, no shadow, and no colored decoration. Horizontally center every subtitle
as a complete block, within the lower safe area, approximately seven percent
above the bottom edge. Maintain identical font size, outline width, alignment,
and vertical position across all subtitle changes. Render the Japanese
characters exactly as written. No translation, romanization, speaker labels,
quotation marks, or additional text anywhere else.`;

const LIP_SYNC = `Her mouth moves precisely with every Japanese syllable she speaks. Her jaw
and lips open and close in exact synchronization with the words, opening
clearly on vowels and closing fully during pauses. Her cheeks, chin, and
throat move naturally with her speech. She blinks naturally.`;

const state = {
  mode: "t2va",
  models: null,
  // Filled from /genso/api/status; null until the engine answers, which keeps
  // the size limits from being presented as fact for an unknown card.
  vramTotalGb: null,
  templates: [],
  selectedLoras: new Map(),
  loraInitialized: false,
  firstFrame: null,
  lastFrame: null,
  refImages: [],
  refVideos: [],
  refAudios: [],
  lastMannequinPrompt: "",
  currentJob: null,
  websocket: null,
  wsRetry: null,
  toastTimer: null,
  editor: null,
  templateDrafts: {},
  promptDrafts: {
    t2va: "",
    fl2va: "",
    ref2va: "",
    mannequin: "",
  },
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; }
  catch { body = { error: text || `HTTP ${response.status}` }; }
  if (!response.ok || body.ok === false) {
    throw new Error(body.error || `HTTP ${response.status}`);
  }
  return body;
}

function toast(message, duration = 3500) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => node.classList.add("hidden"), duration);
}

async function translatePrompt() {
  const prompt = $("#prompt");
  const button = $("#translatePrompt");
  const source = prompt.value.trim();
  if (!source) {
    toast("翻訳する日本語プロンプトを入力してください");
    prompt.focus();
    return;
  }
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "翻訳中…";
  try {
    const result = await requestJson("/genso/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: source }),
    });
    prompt.value = result.prompt;
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    toast(`安全に英語化しました · ${result.translation_fragments}区画 · ${result.model}`, 5000);
  } catch (error) {
    toast(error.message, 7000);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

function backendMode() {
  return state.mode === "mannequin" ? "ref2va" : state.mode;
}

function alignLength(value) {
  let n = Math.max(29, Math.min(Number(value), 362));
  while (n % 17 !== 5) n += 1;
  return n;
}

function formatClock(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const rounded = Math.round(seconds);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const secs = rounded % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}` : `${minutes}:${String(secs).padStart(2, "0")}`;
}

function formatBytes(bytes) {
  if (!bytes) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function inputViewUrl(name) {
  const normalized = name.replaceAll("\\", "/");
  const parts = normalized.split("/");
  const filename = parts.pop();
  return `/view?filename=${encodeURIComponent(filename)}&subfolder=${encodeURIComponent(parts.join("/"))}&type=input`;
}

async function loadStatus() {
  const pill = $("#engineStatus");
  try {
    const status = await requestJson("/genso/api/status");
    state.models = status.models;
    state.vramTotalGb = status.vram_total_gb ?? null;
    updateEstimate();
    pill.className = "engine-pill ready";
    pill.innerHTML = '<span class="status-dot"></span><span>エンジン稼働中</span>';
    $("#engineMeta").textContent = status.vram_free_gb == null ? status.ram_note : `VRAM ${status.vram_free_gb.toFixed(1)}GB 空き · ${status.ram_note}`;
    $("#modelRoot").textContent = `${status.output_dir.replace(/[\\/]output[\\/]GENSO$/i, "")}\\models\\（diffusion_models / text_encoders / vae / loras）`;
    renderModels(status.models);
    renderTextEncoders(status.models);
    renderLoras();
    detectWrapperState();
  } catch (error) {
    pill.className = "engine-pill error";
    pill.innerHTML = '<span class="status-dot"></span><span>接続エラー</span>';
    $("#engineMeta").textContent = "";
    toast(error.message, 6000);
  }
}

function detectWrapperState() {
  const shellMode = new URLSearchParams(location.search).get("shell");
  $("#externalNotice").classList.toggle("hidden", shellMode !== "external");
}

function modelRow(item) {
  return `<div class="model-row"><div><strong title="${escapeHtml(item.file)}">${escapeHtml(item.file)}</strong><small>${escapeHtml(item.folder)} · ${escapeHtml(item.source)}</small></div><span class="badge ${item.present ? "" : "missing"}">${item.present ? "配置済み" : "未配置"}</span></div>`;
}

function renderModels(models) {
  $("#requiredModels").innerHTML = models.required.map(modelRow).join("");
  $("#optionalModels").innerHTML = models.optional.map(modelRow).join("");
  const missing = models.required.some((item) => !item.present);
  $("#setupCard").classList.toggle("hidden", !missing);
  $("#workspace").classList.toggle("hidden", missing);
}

function renderTextEncoders(models) {
  const select = $("#textEncoder");
  const encoders = models.text_encoders || [];
  const previous = select.value;
  select.innerHTML = encoders.map((item) => {
    const format = item.role === "text_encoder_nvfp4" ? "NVFP4 AWQ" : "GGUF Q4_K_M";
    const suffix = item.present ? "" : "（未配置）";
    return `<option value="${escapeHtml(item.name)}" ${item.present ? "" : "disabled"}>${format} · ${escapeHtml(item.file)}${suffix}</option>`;
  }).join("");
  const available = encoders.filter((item) => item.present);
  const selected = available.find((item) => item.name === previous)
    || available.find((item) => item.role === "text_encoder_gguf")
    || available[0];
  if (selected) select.value = selected.name;
}

function setMode(mode) {
  const previousMode = state.mode;
  const prompt = $("#prompt");
  if (prompt && previousMode) state.promptDrafts[previousMode] = prompt.value;
  state.mode = mode;
  if (prompt) prompt.value = state.promptDrafts[mode] || "";
  $$(".mode-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.mode === mode));
  const copy = MODE_COPY[mode];
  $("#modeEyebrow").textContent = copy.eyebrow;
  $("#modeTitle").textContent = copy.title;
  $("#modeHint").textContent = copy.hint;
  $("#imagePanel").classList.toggle("hidden", mode !== "fl2va");
  $("#referencePanel").classList.toggle("hidden", !["ref2va", "mannequin"].includes(mode));
  $("#mannequinIntro").classList.toggle("hidden", mode !== "mannequin");
  $("#bodyOptions").classList.toggle("hidden", mode !== "mannequin");
  $("#refAudioGroup").classList.toggle("hidden", mode === "mannequin");
  $("#refImageSizeField").classList.toggle("hidden", false);
  $$(".ref-tool").forEach((button) => button.classList.toggle("hidden", !["ref2va", "mannequin"].includes(mode)));
  $("#refImageGuide").textContent = mode === "mannequin" ? "キャラ設定画 1〜3枚（正面・側面・背面推奨）" : "正面・背面・顔など、最大9枚";
  updateReferenceLabels();
  updateTemplateOptions();
  updateLoraPolicy(false);
  if (mode === "mannequin") refreshMannequinPrompt(false);
  if (prompt) state.promptDrafts[mode] = prompt.value;
  updateEstimate();
}

function updateTemplateOptions() {
  const select = $("#templateSelect");
  const allowed = state.templates.filter((template) => template.modes.includes(state.mode) || template.modes.includes(backendMode()));
  select.innerHTML = '<option value="">テンプレート ▾</option>' + allowed.map((template) => `<option value="${escapeHtml(template.id)}">${escapeHtml(template.label)}</option>`).join("");
}

function closeStructuredEditor() {
  $("#editorOverlay").classList.add("hidden");
  $("#editorOverlay").setAttribute("aria-hidden", "true");
  document.body.classList.remove("editor-open");
  state.editor = null;
}

function readEditorValues() {
  const values = {};
  for (const field of state.editor?.fields || []) {
    if (field.type === "checkbox") {
      values[field.key] = field.input.checked ? (field.checkedValue ?? true) : "";
    } else {
      values[field.key] = field.input.value.trim();
    }
  }
  return values;
}

function updateEditorPreview() {
  if (!state.editor?.preview) return;
  $("#editorPreview").textContent = state.editor.preview(readEditorValues());
}

function createEditorField(field) {
  const wrapper = document.createElement("label");
  wrapper.className = `editor-field${field.wide ? " wide" : ""}`;
  let input;
  if (field.type === "checkbox") {
    wrapper.classList.add("editor-check");
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(field.value);
    input.disabled = Boolean(field.disabled);
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = field.label;
    copy.append(title);
    if (field.help) {
      const help = document.createElement("small");
      help.textContent = field.help;
      copy.append(help);
    }
    wrapper.append(input, copy);
  } else {
    const label = document.createElement("span");
    label.className = "editor-field-label";
    label.textContent = field.label;
    if (field.type === "textarea") {
      input = document.createElement("textarea");
      input.rows = field.rows || 2;
    } else {
      input = document.createElement("input");
      input.type = field.type || "text";
      if (field.step) input.step = field.step;
      if (field.min != null) input.min = field.min;
    }
    input.value = field.value ?? "";
    input.placeholder = field.placeholder || "";
    input.required = field.required !== false;
    wrapper.append(label, input);
    if (field.help) {
      const help = document.createElement("small");
      help.textContent = field.help;
      wrapper.append(help);
    }
  }
  field.input = input;
  input.addEventListener("input", updateEditorPreview);
  input.addEventListener("change", updateEditorPreview);
  return wrapper;
}

function openStructuredEditor(config) {
  const fieldsRoot = $("#editorFields");
  fieldsRoot.replaceChildren();
  $("#editorEyebrow").textContent = config.eyebrow || "TEMPLATE BUILDER";
  $("#editorTitle").textContent = config.title;
  $("#editorDescription").textContent = config.description || "";
  $("#editorSubmit").textContent = config.submitLabel || "プロンプトへ反映";
  $("#editorHint").textContent = config.hint || "すべての項目を一度に入力できます";

  const groups = new Map();
  for (const field of config.fields) {
    const groupName = field.group || "内容";
    if (!groups.has(groupName)) groups.set(groupName, []);
    groups.get(groupName).push(field);
  }
  for (const [groupName, groupFields] of groups) {
    const section = document.createElement("section");
    section.className = "editor-group";
    const heading = document.createElement("div");
    heading.className = "editor-group-title";
    heading.textContent = groupName;
    const grid = document.createElement("div");
    grid.className = "editor-group-grid";
    groupFields.forEach((field) => grid.append(createEditorField(field)));
    section.append(heading, grid);
    fieldsRoot.append(section);
  }

  state.editor = { ...config };
  $("#editorPreviewPanel").classList.toggle("hidden", !config.preview);
  updateEditorPreview();
  $("#editorOverlay").classList.remove("hidden");
  $("#editorOverlay").setAttribute("aria-hidden", "false");
  document.body.classList.add("editor-open");
  requestAnimationFrame(() => config.fields.find((field) => !field.disabled)?.input?.focus());
}

function templatePlaceholders(body) {
  return [...new Set([...body.matchAll(/\{\{([^{}]+)\}\}/g)].map((match) => match[1]))];
}

function templateAutomaticValues(template) {
  const values = {};
  if (template.id === "ref2va_declaration") {
    const allPictures = pictureTags();
    values[", <Picture 2> and <Picture 3>"] = allPictures.startsWith("<Picture 1>")
      ? allPictures.slice("<Picture 1>".length) : "";
  }
  if (template.id === "mannequin_chibi") {
    values.img_refs = pictureTags() || "<Picture 1>";
  }
  return values;
}

function templateDefaultValue(key) {
  const duration = Number($("#length").value) / 24;
  const defaults = {
    t1: (duration / 3).toFixed(1),
    t2: (duration * 2 / 3).toFixed(1),
    t3: duration.toFixed(1),
    start: "0.0",
    end: Math.min(3, duration).toFixed(1),
    character: "character",
    bgm_or_NA: "NA",
    subject_desc: "main character",
  };
  if (key === "<Audio 1> is the voice-timbre reference for <Subject 1> (S1).") {
    return state.refAudios.length > 0 || state.refVideos.some((item) => item.useAudio);
  }
  // In ref2va, Picture tags are appearance references by default. The
  // received workflow warns that an explicit first-frame instruction changes
  // their meaning, so require a deliberate opt-in instead of prechecking it.
  if (key === "Use <Picture 1> as the first frame, exactly as it is.") return false;
  if (key === "Apply the motion from <Video 1>.") return state.refVideos.length > 0;
  return defaults[key] ?? "";
}

function renderTemplate(body, values) {
  return body.replace(/\{\{([^{}]+)\}\}/g, (_match, key) => values[key] ?? "");
}

function openTemplateEditor(template) {
  const automatic = templateAutomaticValues(template);
  const draft = state.templateDrafts[template.id] || {};
  const keys = templatePlaceholders(template.body).filter((key) => !(key in automatic));
  const fields = keys.map((key) => {
    const meta = TEMPLATE_FIELD_META[key] || {};
    const type = meta.type || "textarea";
    const value = key in draft ? draft[key] : templateDefaultValue(key);
    const unavailableAudio = key.startsWith("<Audio 1>")
      && !state.refAudios.length && !state.refVideos.some((item) => item.useAudio);
    const unavailablePicture = key.startsWith("Use <Picture 1>") && !state.refImages.length;
    const unavailableVideo = key.startsWith("Apply the motion") && !state.refVideos.length;
    return {
      key,
      label: meta.label || key.replaceAll("_", " "),
      group: meta.group || "内容",
      type,
      rows: meta.rows,
      step: meta.step,
      min: meta.min,
      placeholder: meta.placeholder,
      help: meta.help,
      checkedValue: type === "checkbox" ? key : undefined,
      value: (unavailableAudio || unavailablePicture || unavailableVideo) ? false : value,
      disabled: unavailableAudio || unavailablePicture || unavailableVideo,
      wide: meta.wide ?? (type === "textarea" && (meta.rows || 2) >= 3),
    };
  });
  const mergedValues = (values) => ({ ...automatic, ...values });
  if (!fields.length) {
    $("#prompt").value = renderTemplate(template.body, automatic);
    toast(`「${template.label}」を反映しました`);
    return;
  }
  openStructuredEditor({
    title: template.label,
    description: TEMPLATE_DESCRIPTIONS[template.id] || "必要な内容をまとめて入力します。",
    fields,
    preview: (values) => renderTemplate(template.body, mergedValues(values)),
    onSubmit: (values) => {
      if (template.id === "t2va_storyboard"
        && !(Number(values.t1) < Number(values.t2) && Number(values.t2) < Number(values.t3))) {
        throw new Error("ショット終了秒は t1 < t2 < t3 の順にしてください");
      }
      if (template.id === "ref2va_subtitle" && Number(values.end) <= Number(values.start)) {
        throw new Error("字幕の終了秒は開始秒より後にしてください");
      }
      state.templateDrafts[template.id] = values;
      $("#prompt").value = renderTemplate(template.body, mergedValues(values));
      toast(`「${template.label}」を反映しました`);
    },
  });
}

function insertAtCursor(text, prefix = "", suffix = "") {
  const area = $("#prompt");
  const start = area.selectionStart ?? area.value.length;
  const end = area.selectionEnd ?? start;
  const before = area.value.slice(0, start);
  const after = area.value.slice(end);
  const leading = before && !before.endsWith("\n") ? "\n\n" : "";
  const trailing = after && !after.startsWith("\n") ? "\n\n" : "";
  area.value = prefix + before + leading + text + trailing + after + suffix;
  area.focus();
  const position = (prefix + before + leading + text).length;
  area.setSelectionRange(position, position);
}

function pictureTags() {
  const tags = state.refImages.map((_item, index) => `<Picture ${index + 1}>`);
  if (tags.length < 2) return tags[0] || "";
  return `${tags.slice(0, -1).join(", ")} and ${tags.at(-1)}`;
}

function editorField(key, value = "", overrides = {}) {
  const meta = TEMPLATE_FIELD_META[key] || {};
  const type = overrides.type || meta.type || "textarea";
  return {
    key,
    label: overrides.label || meta.label || key,
    group: overrides.group || meta.group || "内容",
    type,
    rows: overrides.rows || meta.rows,
    step: overrides.step || meta.step,
    min: overrides.min ?? meta.min,
    placeholder: overrides.placeholder || meta.placeholder,
    help: overrides.help || meta.help,
    checkedValue: overrides.checkedValue,
    value,
    required: overrides.required,
    wide: overrides.wide ?? (type === "textarea"),
  };
}

function buildDeclaration(values) {
  const lines = [];
  if (state.refImages.length) {
    lines.push(`<Subject 1> is the ${values.subject || "main character"} in ${pictureTags()}.`);
    lines.push("Retention: <Subject 1> is attribute_transfer.");
  }
  if (values.useAudio) lines.push("<Audio 1> is the voice-timbre reference for <Subject 1> (S1).");
  if (values.useVideo) lines.push("Apply the motion from <Video 1>.");
  return lines.join("\n");
}

function insertDeclaration() {
  if (!state.refImages.length && !state.refVideos.length && !state.refAudios.length) {
    toast("先に参照ファイルを追加してください");
    return;
  }
  const hasAudio = state.refAudios.length > 0 || state.refVideos.some((item) => item.useAudio);
  const fields = [];
  if (state.refImages.length) {
    fields.push(editorField("subject", "main character", {
      label: "Subject 1 の説明", group: "人物", type: "text", wide: true,
      placeholder: "例：main character / Shirona",
    }));
  }
  if (hasAudio) {
    fields.push(editorField("useAudio", true, {
      label: "音声を声色参照として使う", group: "適用する参照", type: "checkbox",
      checkedValue: true, required: false, wide: false,
    }));
  }
  if (state.refVideos.length) {
    fields.push(editorField("useVideo", true, {
      label: "Video 1 の動きを適用", group: "適用する参照", type: "checkbox",
      checkedValue: true, required: false, wide: false,
    }));
  }
  openStructuredEditor({
    eyebrow: "REFERENCE DECLARATION",
    title: "参照の宣言文",
    description: "追加済みファイルからタグ番号を自動生成します。使う参照だけを選んでください。",
    fields,
    preview: buildDeclaration,
    onSubmit: (values) => insertAtCursor(buildDeclaration(values)),
    submitLabel: "宣言文を挿入",
  });
}

function insertSubtitle() {
  const duration = Number($("#length").value) / 24;
  const makeBlock = (values) => `From ${values.start} to ${values.end}, <Subject 1> (S1) says in the same referenced voice:
<d>[Japanese] ${values.serifu}</d>
At exactly the same time, display the Japanese subtitle "${values.serifu}"
at the bottom center of the screen. The subtitle appears when the first
syllable begins and disappears immediately after the final syllable ends.`;
  openStructuredEditor({
    eyebrow: "JAPANESE SUBTITLE",
    title: "日本語字幕ブロック",
    description: "台詞は音声と字幕へ同じ原文を使います。翻訳器からも保護されます。",
    fields: [
      editorField("start", "0.0", { wide: false }),
      editorField("end", Math.min(3, duration).toFixed(1), { wide: false }),
      editorField("serifu", "", { wide: true }),
    ],
    preview: makeBlock,
    onSubmit: (values) => {
      if (Number(values.end) <= Number(values.start)) throw new Error("終了秒は開始秒より後にしてください");
      const area = $("#prompt");
      const block = makeBlock(values);
      area.value = area.value.replace(`\n\n${SUBTITLE_STYLE}`, "").replace(SUBTITLE_STYLE, "").trimEnd();
      area.value = `${area.value}${area.value ? "\n\n" : ""}${block}\n\n${SUBTITLE_STYLE}`;
    },
    submitLabel: "字幕ブロックを挿入",
  });
}

function insertDialogue() {
  const makeBlock = (values) => `<d>[Japanese] ${values.serifu}</d>`;
  openStructuredEditor({
    eyebrow: "DIALOGUE",
    title: "台詞を挿入",
    description: "入力した日本語をそのまま発話させます。翻訳時にも一字一句保持されます。",
    fields: [editorField("serifu", "", { wide: true })],
    preview: makeBlock,
    onSubmit: (values) => {
      const area = $("#prompt");
      const declaration = area.value.includes("(S1)") ? "" : "The on-screen character is <Subject 1> (S1).\n\n";
      insertAtCursor(makeBlock(values), declaration);
    },
    submitLabel: "台詞を挿入",
  });
}

function mannequinPrompt(chibi) {
  const refs = pictureTags() || "<Picture 1>";
  const proportion = chibi ? `
Prioritize <Picture 1>'s body proportions over the mannequin's —
do not follow the mannequin's adult proportions.
<Picture 1> has a chibi/child-like body type: large head,
short torso, and short limbs. Redraw the character to match
this proportion, not the mannequin's.` : "";
  return `Apply the mannequin's motion from the input video <Video 1> to the character in ${refs}.${proportion}
Draw hair and clothing from scratch based on <Picture 1>,
with natural physics-based movement (swaying, bouncing)
that follows the motion.
Render in Japanese cel-shaded anime style, limited animation
at 24fps (on 2s / on 3s timing), thick outlines, flat coloring.
Follow the input video's motion and camera work exactly.`;
}

function refreshMannequinPrompt(force) {
  if (state.mode !== "mannequin") return;
  const area = $("#prompt");
  if (!force && area.value.trim() && area.value !== state.lastMannequinPrompt) return;
  const chibi = $("input[name=bodyType]:checked")?.value === "chibi";
  const generated = mannequinPrompt(chibi);
  area.value = generated;
  state.lastMannequinPrompt = generated;
  state.promptDrafts.mannequin = generated;
}

function getDimensions() {
  if ($("#customSize").checked) return [Number($("#customWidth").value), Number($("#customHeight").value)];
  if ($("#aspectRatio").value === "source" && state.firstFrame?.width && state.firstFrame?.height) {
    return dimensionsForSourceAspect(state.firstFrame.width, state.firstFrame.height, $("#quality").value);
  }
  return RESOLUTIONS[$("#aspectRatio").value][$("#quality").value];
}

function greatestCommonDivisor(left, right) {
  let a = Math.abs(Math.round(left));
  let b = Math.abs(Math.round(right));
  while (b) [a, b] = [b, a % b];
  return a || 1;
}

function sourceAspectText(width, height) {
  const divisor = greatestCommonDivisor(width, height);
  const left = width / divisor;
  const right = height / divisor;
  return left <= 100 && right <= 100 ? `${left}:${right}` : `${(width / height).toFixed(3)}:1`;
}

function dimensionsForSourceAspect(sourceWidth, sourceHeight, quality) {
  const ratio = sourceWidth / sourceHeight;
  const [referenceWidth, referenceHeight] = RESOLUTIONS["16:9"][quality] || RESOLUTIONS["16:9"].draft;
  const targetArea = referenceWidth * referenceHeight;
  const maxDimension = { draft: 768, standard: 1024, high: 1536 }[quality] || 768;
  let best = null;
  for (let width = 64; width <= maxDimension; width += 32) {
    for (let height = 64; height <= maxDimension; height += 32) {
      const aspectError = Math.abs(Math.log((width / height) / ratio));
      const areaError = Math.abs(Math.log((width * height) / targetArea));
      const score = aspectError * 12 + areaError;
      if (!best || score < best.score) best = { width, height, score };
    }
  }
  return [best.width, best.height];
}

function updateResolution() {
  const [width, height] = getDimensions();
  $("#resolutionLabel").textContent = `${width} × ${height}`;
  const length = Number($("#length").value);
  $("#durationLabel").textContent = `${(length / 24).toFixed(1)}秒 / ${length}フレーム`;
  $("#customWidth").disabled = !$("#customSize").checked;
  $("#customHeight").disabled = !$("#customSize").checked;
  // Only the hard input error lives here now. How heavy a size is for this
  // card is judged by token count in updateEstimate(), which knows the actual
  // VRAM — guessing from the quality preset duplicated it and was often wrong.
  const invalid = width % 32 !== 0 || height % 32 !== 0;
  const warning = $("#resolutionWarning");
  warning.classList.toggle("hidden", !invalid);
  warning.textContent = "カスタム幅と高さは32の倍数にしてください";
  const aspectNote = $("#aspectMatchNote");
  const sourceMatched = !$("#customSize").checked && $("#aspectRatio").value === "source" && state.firstFrame;
  aspectNote.classList.toggle("hidden", !sourceMatched);
  if (sourceMatched) {
    aspectNote.textContent = `入力 ${state.firstFrame.width}×${state.firstFrame.height} の比率 ${sourceAspectText(state.firstFrame.width, state.firstFrame.height)} を維持`;
  }
  paintKeyframeAspectWarning(width, height);
  updateEstimate();
}

// Two independent ceilings on an 8 GiB card. Cross either one and the driver
// silently spills the overflow into shared system memory, where the GPU reads
// it over PCIe at a fraction of VRAM bandwidth.
//
// Frames — measured at 864x480: 39f/4min, 73f/7min, 90f/9min, 107f/10min all
// scale linearly, while 124f came in at 14, 19, 19, 34, 48 and 65 minutes for
// the same job. 107 is the last stable value on H3's 17k+5 grid.
//
// Pixels — tokens per frame is (W/16)*(H/16). 1584 (576x704) and 1620 (864x480)
// both run at ~70 s/step; 2000 (640x800) never finished a step, and cutting
// frames did not rescue it. This ceiling is per frame, so it cannot be traded
// against length.
const SAFE_LENGTH = 107;
const SAFE_TOKENS_PER_FRAME = 1620;
// The card these numbers came from. Anything meaningfully larger is out of
// scope for them.
const MEASURED_VRAM_GB = 8;

function tokensPerFrame(width, height) {
  return Math.floor(width / 16) * Math.floor(height / 16);
}

function updateEstimate() {
  const [width, height] = getDimensions();
  const quality = $("#customSize").checked
    ? "カスタム"
    : ({ draft: "下書き", standard: "標準", high: "高精細" }[$("#quality").value] || "設定済み");
  const frames = Number($("#length").value) || 0;
  $("#estimate").textContent = `${quality} · ${width}×${height} · ${frames}f`;
  $("#estimate").title = "所要時間はVRAM・RAMへの退避量とモデル初期化状態で大きく変わるため予測しません";

  const note = $("#lengthNote");
  if (!note) return;
  const tokens = tokensPerFrame(width, height);
  const vram = state.vramTotalGb;

  // The ceilings below were measured on an 8 GiB card, where the 21 GB model
  // cannot fit and the overflow crosses PCIe every step. A larger card has a
  // different — and untested — limit, so it gets the numbers as information
  // rather than as a warning.
  if (vram != null && vram > MEASURED_VRAM_GB + 1.5) {
    note.textContent = `VRAM ${vram.toFixed(0)}GB · 1フレーム${tokens}トークン / ${frames}フレーム（8GB機の実測上限は1620トークン・107フレーム。この容量での上限は未検証です）`;
    note.classList.remove("warn");
    return;
  }

  if (tokens > SAFE_TOKENS_PER_FRAME) {
    // Resolution is the harder wall: shortening the clip does not help, because
    // the activation buffer is sized per frame.
    note.textContent = `⚠ ${width}×${height} は8GBのVRAMに対して大きすぎます（1フレーム${tokens}トークン / 目安${SAFE_TOKENS_PER_FRAME}）。この場合はフレーム数を減らしても速くなりません。864×480 か 576×704 まで下げてください。`;
    note.classList.add("warn");
  } else if (frames > SAFE_LENGTH) {
    note.textContent = `⚠ ${frames}フレームは8GBのVRAMには多すぎます。${SAFE_LENGTH}フレーム（${(SAFE_LENGTH / 24).toFixed(2)}秒）までなら約10分ですが、超えると実測で14〜65分とばらつきます。短く分けて作り、あとで繋ぐほうが確実です。`;
    note.classList.add("warn");
  } else if (vram == null) {
    note.textContent = `1フレーム${tokens}トークン / ${frames}フレーム（VRAM 8GB機での実測上限は1620トークン・107フレーム）`;
    note.classList.remove("warn");
  } else {
    note.textContent = `VRAM ${vram.toFixed(0)}GB で安定して回せる範囲です（1フレーム${tokens}トークン / 上限${SAFE_TOKENS_PER_FRAME}、${frames}/${SAFE_LENGTH}フレーム）`;
    note.classList.remove("warn");
  }
}

/* The engine fits the two keyframes differently — the first is stretched to
   the canvas (crop="disabled") and the last is centre-cropped to it
   (crop="center", nodes_minimax_h3.py:146,151). Dropping a first frame already
   sets the canvas to its aspect, but nothing ever checked the LAST frame, so a
   tail of a different shape was silently trimmed. */
const KEYFRAME_ASPECT_TOLERANCE = 0.02;

function keyframeAspectOff(frame, width, height) {
  if (!frame?.width || !frame?.height || !width || !height) return false;
  return Math.abs(Math.log((frame.width / frame.height) / (width / height)))
    > KEYFRAME_ASPECT_TOLERANCE;
}

function paintKeyframeAspectWarning(width, height) {
  const node = $("#keyframeAspectWarning");
  if (!node) return;
  const messages = [];
  if (keyframeAspectOff(state.firstFrame, width, height)) {
    messages.push(
      `開始フレーム ${state.firstFrame.width}×${state.firstFrame.height} は `
      + `${width}×${height} へ引き伸ばされます（縦横が歪みます）`
    );
  }
  if (keyframeAspectOff(state.lastFrame, width, height)) {
    messages.push(
      `終了フレーム ${state.lastFrame.width}×${state.lastFrame.height} は `
      + `中央を切り抜いて ${width}×${height} にされます（端が切れます）`
    );
  }
  node.classList.toggle("hidden", messages.length === 0);
  if (messages.length) {
    node.innerHTML = messages.map((text) => escapeHtml(text)).join("<br>")
      + '<br>「画像に合わせる」で比率を揃えられます。';
  }
}

function matchFirstImage(showToast = true) {
  if (!state.firstFrame?.width || !state.firstFrame?.height) {
    toast("先に開始フレームを選択してください");
    return;
  }
  const option = $("#sourceAspectOption");
  option.hidden = false;
  option.textContent = `入力画像 ${sourceAspectText(state.firstFrame.width, state.firstFrame.height)}`;
  $("#customSize").checked = false;
  $("#aspectRatio").value = "source";
  updateResolution();
  if (showToast) {
    const [width, height] = getDimensions();
    toast(`入力画像の比率に合わせました（${width}×${height}）`);
  }
}

function renderLoras() {
  if (!state.models) return;
  const availableNames = new Set(state.models.loras.map((item) => item.name));
  [...state.selectedLoras.keys()].forEach((name) => {
    if (!availableNames.has(name)) state.selectedLoras.delete(name);
  });
  if (!state.loraInitialized) {
    const standard = state.models.loras.find((item) => item.name.toLowerCase().endsWith("minimax_h3_turbo_4step_ckpt600_ema_v4.safetensors"));
    if (standard) state.selectedLoras.set(standard.name, Number(standard.known?.default_strength ?? 1));
    state.loraInitialized = true;
  }
  const root = $("#loraList");
  if (!state.models.loras.length) {
    root.innerHTML = '<div class="micro-note">MiniMax-H3 対応LoRAは見つかりませんでした。高速化なしの20ステップ設定になります。</div>';
    updateLoraPolicy(true);
    return;
  }
  root.innerHTML = "";
  state.models.loras.forEach((item, index) => {
    const checked = state.selectedLoras.has(item.name);
    const defaultStrength = Number(item.known?.default_strength ?? 1);
    const selectedStrength = Number(state.selectedLoras.get(item.name) ?? defaultStrength);
    const row = document.createElement("div");
    row.className = "lora-row";
    row.innerHTML = `<input type="checkbox" ${checked ? "checked" : ""} aria-label="${escapeHtml(item.name)}を使う">
      <div class="lora-name"><strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong><small>${escapeHtml(item.known?.note || "MiniMax-H3 対応LoRA")}</small></div>
      <input type="range" min="0" max="1.5" step="0.05" value="${selectedStrength}" ${checked ? "" : "disabled"} aria-label="強度">
      <span class="lora-strength">${selectedStrength.toFixed(2)}</span>`;
    const checkbox = $("input[type=checkbox]", row);
    const range = $("input[type=range]", row);
    const strength = $(".lora-strength", row);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedLoras.set(item.name, Number(range.value));
      else state.selectedLoras.delete(item.name);
      range.disabled = !checkbox.checked;
      updateLoraPolicy(true);
      renderLoraWarnings();
    });
    range.addEventListener("input", () => {
      strength.textContent = Number(range.value).toFixed(2);
      if (checkbox.checked) state.selectedLoras.set(item.name, Number(range.value));
    });
    row.dataset.index = index;
    root.append(row);
  });
  updateLoraPolicy(true);
  renderLoraWarnings();
}

function selectedLoraRecords() {
  return [...state.selectedLoras.entries()].map(([name, strength]) => {
    const model = state.models?.loras.find((item) => item.name === name);
    return { name, strength, known: model?.known || null };
  });
}

function updateLoraPolicy(applySettings) {
  const selected = selectedLoraRecords();
  const turbo = selected.find((item) => item.known?.kind === "turbo");
  if (applySettings) {
    if (turbo) {
      $("#steps").value = turbo.known.steps ?? 8;
      $("#sampler").value = turbo.known.sampler ?? "res_multistep";
    } else {
      $("#steps").value = 20;
      $("#sampler").value = "res_multistep";
    }
  }
  $("#loraCount").textContent = `${selected.length} 選択`;
  renderLoraWarnings();
  updateEstimate();
}

function renderLoraWarnings() {
  const selected = selectedLoraRecords();
  const messages = [];
  if (selected.length >= 3) messages.push("LoRA の併用は2つまでを推奨。強度の高い併用は音のこもり・動きの不自然化を招きます");
  if (selected.some((item) => item.known?.kind === "turbo") && selected.some((item) => item.known?.kind !== "turbo")) {
    messages.push("高速化 LoRA と他の LoRA の併用は品質が落ちることがあります。品質重視なら高速化を外して20ステップにしてください");
  }
  const currentMode = backendMode();
  selected.forEach((item) => {
    if (item.known?.modes && !item.known.modes.includes(currentMode)) messages.push(`${item.name} は現在のモード向けではありません`);
  });
  $("#loraWarnings").innerHTML = [...new Set(messages)].map((message) => `<div class="notice warn">${escapeHtml(message)}</div>`).join("");
}

async function uploadFile(file) {
  const form = new FormData();
  form.append("image", file, file.name);
  form.append("type", "input");
  form.append("overwrite", "true");
  const result = await requestJson("/upload/image", { method: "POST", body: form });
  return [result.subfolder, result.name].filter(Boolean).join("/");
}

async function imageDimensions(file) {
  if (window.createImageBitmap) {
    const bitmap = await createImageBitmap(file);
    const result = { width: bitmap.width, height: bitmap.height };
    bitmap.close();
    return result;
  }
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => { resolve({ width: image.naturalWidth, height: image.naturalHeight }); URL.revokeObjectURL(image.src); };
    image.onerror = reject;
    image.src = URL.createObjectURL(file);
  });
}

async function setFrame(which, file) {
  if (!file) return;
  try {
    const [width, height] = getDimensions();
    const prepared = await GensoFrameCrop.prepare(file, width, height);
    if (!prepared) return;
    const dimensions = { width, height };
    const name = await uploadFile(prepared);
    state[which === "first" ? "firstFrame" : "lastFrame"] = { name, originalName: file.name, ...dimensions };
    renderFrame(which);
    toast(`${width}×${height} の画像を追加しました`);
  } catch (error) { toast(error.message, 6000); }
}

function renderFrame(which) {
  const item = state[which === "first" ? "firstFrame" : "lastFrame"];
  const node = $(which === "first" ? "#firstFramePreview" : "#lastFramePreview");
  if (!item) {
    node.className = "file-preview empty";
    node.textContent = "未選択";
    return;
  }
  node.className = "file-preview";
  node.innerHTML = `<img src="${inputViewUrl(item.name)}" alt=""><span class="preview-caption">${escapeHtml(item.originalName)} · ${item.width}×${item.height}</span><button class="preview-remove" type="button" title="削除">×</button>`;
  $(".preview-remove", node).addEventListener("click", () => {
    state[which === "first" ? "firstFrame" : "lastFrame"] = null;
    renderFrame(which);
    if (which === "first") {
      $("#sourceAspectOption").hidden = true;
      if ($("#aspectRatio").value === "source") $("#aspectRatio").value = "16:9";
      updateResolution();
    }
  });
}

async function addReferenceImages(files) {
  const limit = state.mode === "mannequin" ? 3 : 9;
  for (const file of [...files].slice(0, Math.max(0, limit - state.refImages.length))) {
    try {
      toast(`${file.name} をアップロード中…`, 60000);
      const dimensions = await imageDimensions(file);
      const name = await uploadFile(file);
      state.refImages.push({ name, originalName: file.name, ...dimensions });
    } catch (error) { toast(`${file.name}: ${error.message}`, 6000); }
  }
  renderReferences();
  refreshMannequinPrompt(false);
  toast("参照画像を追加しました");
}

async function preprocessVideoItem(item, force = false) {
  const [width, height] = getDimensions();
  if (!force && item.targetWidth === width && item.targetHeight === height && item.name) return item;
  item.processing = true;
  renderReferences();
  const result = await requestJson("/genso/api/preprocess_video", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: item.originalUpload, target_width: width, target_height: height }),
  });
  Object.assign(item, {
    name: result.name, fps: result.fps, duration: result.duration_sec,
    width: result.width, height: result.height, note: result.note,
    hasAudio: Boolean(result.has_audio),
    targetWidth: width, targetHeight: height, processing: false,
  });
  return item;
}

async function addReferenceVideos(files) {
  const limit = state.mode === "mannequin" ? 1 : 3;
  for (const file of [...files].slice(0, Math.max(0, limit - state.refVideos.length))) {
    const item = { originalName: file.name, name: "", originalUpload: "", useAudio: false, processing: true };
    state.refVideos.push(item);
    renderReferences();
    try {
      toast(`${file.name} をアップロード・前処理中…`, 60000);
      item.originalUpload = await uploadFile(file);
      await preprocessVideoItem(item, true);
      toast(`${file.name}: ${item.note}`);
    } catch (error) {
      state.refVideos = state.refVideos.filter((entry) => entry !== item);
      toast(`${file.name}: ${error.message}`, 7000);
    }
  }
  renderReferences();
  refreshMannequinPrompt(false);
}

async function addReferenceAudios(files) {
  for (const file of [...files].slice(0, Math.max(0, 3 - state.refAudios.length))) {
    try {
      toast(`${file.name} をアップロード中…`, 60000);
      const name = await uploadFile(file);
      const info = await requestJson(`/genso/api/probe?name=${encodeURIComponent(name)}`);
      if (info.kind !== "audio") throw new Error("音声ファイルとして読み取れません");
      state.refAudios.push({ name, originalName: file.name, duration: info.duration_sec });
    } catch (error) { toast(`${file.name}: ${error.message}`, 7000); }
  }
  renderReferences();
  alignDurationToAudio();
}

function alignDurationToAudio() {
  if (!$("#alignAudio").checked || !state.refAudios.length) return;
  const frames = alignLength(Math.ceil(Math.max(...state.refAudios.map((item) => item.duration)) * 24));
  const select = $("#length");
  let option = [...select.options].find((entry) => Number(entry.value) === frames);
  if (!option) {
    option = new Option(`${(frames / 24).toFixed(1)}秒 · ${frames}f（音声）`, String(frames));
    option.dataset.dynamic = "true";
    select.add(option);
  }
  select.value = String(frames);
  updateResolution();
}

function updateReferenceLabels() {
  const imageLimit = state.mode === "mannequin" ? 3 : 9;
  const videoLimit = state.mode === "mannequin" ? 1 : 3;
  $("#refImageTitle").innerHTML = `${state.mode === "mannequin" ? "キャラ設定画" : "参照画像"} <span>${state.refImages.length} / ${imageLimit}</span>`;
  $("#refVideoTitle").innerHTML = `${state.mode === "mannequin" ? "マネキン動画" : "参照動画"} <span>${state.refVideos.length} / ${videoLimit}</span>`;
  $("#refAudioCount").textContent = `${state.refAudios.length} / 3`;
}

function renderReferences() {
  updateReferenceLabels();
  $("#refImages").innerHTML = state.refImages.map((item, index) => `<div class="file-chip"><img src="${inputViewUrl(item.name)}" alt=""><div class="file-info"><strong>${escapeHtml(item.originalName)}</strong><small>${item.width}×${item.height} · Picture ${index + 1}</small></div><button class="remove" type="button" data-remove-image="${index}">×</button></div>`).join("");
  $$('[data-remove-image]').forEach((button) => button.addEventListener("click", () => {
    state.refImages.splice(Number(button.dataset.removeImage), 1); renderReferences(); refreshMannequinPrompt(false); updateEstimate();
  }));

  $("#refVideos").innerHTML = state.refVideos.map((item, index) => `<div class="file-chip"><div class="file-icon">${item.processing ? "…" : "VID"}</div><div class="file-info"><strong>${escapeHtml(item.originalName)}</strong><small>${item.processing ? "24fpsへ前処理中" : `${item.width}×${item.height} · 24fps · ${Number(item.duration).toFixed(1)}秒 · Video ${index + 1}`}</small><label class="check-row"><input type="checkbox" data-video-audio="${index}" ${item.useAudio ? "checked" : ""} ${item.processing || !item.hasAudio ? "disabled" : ""}><span>${item.processing || item.hasAudio ? "この動画の音声も参照する" : "音声トラックなし"}</span></label></div><button class="remove" type="button" data-remove-video="${index}">×</button></div>`).join("");
  $$('[data-video-audio]').forEach((input) => input.addEventListener("change", () => { state.refVideos[Number(input.dataset.videoAudio)].useAudio = input.checked; }));
  $$('[data-remove-video]').forEach((button) => button.addEventListener("click", () => {
    state.refVideos.splice(Number(button.dataset.removeVideo), 1); renderReferences(); refreshMannequinPrompt(false); updateEstimate();
  }));

  $("#refAudios").innerHTML = state.refAudios.map((item, index) => `<div class="file-chip"><div class="file-icon">AUD</div><div class="file-info"><strong>${escapeHtml(item.originalName)}</strong><small>${Number(item.duration).toFixed(2)}秒 · Audio ${index + 1}</small></div><button class="remove" type="button" data-remove-audio="${index}">×</button></div>`).join("");
  $$('[data-remove-audio]').forEach((button) => button.addEventListener("click", () => {
    state.refAudios.splice(Number(button.dataset.removeAudio), 1); renderReferences(); updateEstimate();
  }));
  $("#insertDeclaration").disabled = !state.refImages.length && !state.refVideos.length && !state.refAudios.length;
  updateEstimate();
}

async function ensureReferenceVideos() {
  for (const item of state.refVideos) await preprocessVideoItem(item);
  renderReferences();
}

function generationPayload() {
  const [width, height] = getDimensions();
  const seedText = $("#seed").value.trim();
  return {
    mode: backendMode(), prompt: $("#prompt").value, width, height,
    length: Number($("#length").value), steps: Number($("#steps").value),
    seed: seedText === "" ? null : Number(seedText), sampler: $("#sampler").value,
    scheduler: $("#scheduler").value, shift_video: Number($("#shiftVideo").value),
    shift_audio: Number($("#shiftAudio").value), gguf: $("#gguf").checked,
    text_encoder: $("#textEncoder").value,
    loras: selectedLoraRecords().map(({ name, strength }) => ({ name, strength })),
    first_frame: state.mode === "fl2va" ? state.firstFrame?.name || null : null,
    last_frame: state.mode === "fl2va" ? state.lastFrame?.name || null : null,
    ref_images: ["ref2va", "mannequin"].includes(state.mode) ? state.refImages.map((item) => item.name) : [],
    ref_videos: ["ref2va", "mannequin"].includes(state.mode) ? state.refVideos.map((item) => item.name) : [],
    ref_video_use_audio: ["ref2va", "mannequin"].includes(state.mode) ? state.refVideos.map((item) => item.useAudio) : [],
    ref_audios: state.mode === "ref2va" ? state.refAudios.map((item) => item.name) : [],
    ref_image_size: $("input[name=refImageSize]:checked")?.value || "match",
    align_audio: $("#alignAudio").checked,
    out_name: $("#outName").value,
  };
}

function validateBeforeGenerate(payload) {
  if (!payload.prompt.trim()) throw new Error("プロンプトを入力してください");
  if (payload.width % 32 || payload.height % 32) throw new Error("幅と高さは32の倍数にしてください");
  if (state.mode === "fl2va" && !state.firstFrame) throw new Error("開始フレームを選択してください");
  if (backendMode() === "ref2va" && !payload.ref_images.length && !payload.ref_videos.length && !payload.ref_audios.length) throw new Error("参照ファイルを1つ以上追加してください");
  if (state.mode === "mannequin" && (!payload.ref_videos.length || !payload.ref_images.length)) throw new Error("マネキン動画とキャラ設定画を追加してください");
}

async function generate() {
  if (state.currentJob) return;
  const button = $("#generateButton");
  try {
    button.disabled = true;
    button.firstElementChild.textContent = "準備中…";
    if (state.mode === "fl2va") {
      const [width, height] = getDimensions();
      for (const which of ["first", "last"]) {
        const key = which === "first" ? "firstFrame" : "lastFrame";
        const item = state[key];
        if (!item || (item.width === width && item.height === height)) continue;
        const response = await fetch(inputViewUrl(item.name));
        if (!response.ok) throw new Error("画像を読み込めませんでした");
        const file = new File([await response.blob()], "keyframe.png", { type: "image/png" });
        const prepared = await GensoFrameCrop.prepare(file, width, height);
        if (!prepared) return;
        state[key] = { ...item, name: await uploadFile(prepared), width, height };
        renderFrame(which);
      }
    }
    if (["ref2va", "mannequin"].includes(state.mode)) await ensureReferenceVideos();
    const payload = generationPayload();
    validateBeforeGenerate(payload);
    startProgress();
    const result = await requestJson("/genso/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-GENSO-Source": "ui" },
      body: JSON.stringify(payload),
    });
    $("#seed").value = String(result.settings.seed);
    state.currentJob = {
      id: result.prompt_id, started: performance.now(), stage: "開始待ち", step: 0,
      total: result.settings.steps, lastStep: 0, lastStepAt: 0, stepTimes: [],
    };
    $("#interruptButton").classList.remove("hidden");
    updateProgressUi();
    monitorHistory(result.prompt_id);
  } catch (error) {
    failProgress(error.message);
  } finally {
    button.disabled = false;
    button.firstElementChild.textContent = "生成する";
  }
}

function startProgress() {
  $("#progressCard").classList.add("running");
  $("#progressStage").textContent = "生成を投入中";
  $("#stepLabel").textContent = "準備中";
  $("#timeLabel").textContent = "入力を検証しています";
  $("#progressFill").style.width = "1%";
  $("#progressHalo").style.width = "1%";
  $("#errorGuide").classList.add("hidden");
  $("#slowWarning").classList.add("hidden");
}

function updateProgressUi() {
  const job = state.currentJob;
  if (!job) return;
  const elapsed = (performance.now() - job.started) / 1000;
  if (job.stepTimes.length && job.total) {
    const average = job.stepTimes.reduce((sum, value) => sum + value, 0) / job.stepTimes.length;
    $("#slowWarning").classList.toggle("hidden", average <= 150);
  }
  $("#progressStage").textContent = job.stage;
  $("#stepLabel").textContent = job.total ? `step ${job.step} / ${job.total}` : "処理中";
  $("#timeLabel").textContent = `経過 ${formatClock(elapsed)}`;
  const percent = job.total && job.step ? Math.min(92, Math.max(3, job.step / job.total * 88)) : 2;
  $("#progressFill").style.width = `${percent}%`;
  $("#progressHalo").style.width = `${percent}%`;
}

function connectWebSocket() {
  clearTimeout(state.wsRetry);
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/ws?clientId=genso`);
  state.websocket = ws;
  ws.onmessage = (event) => {
    if (typeof event.data !== "string") return;
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    const data = message.data || {};
    // Shared-session events arrive whether or not this window started the job,
    // so they are handled before the "is this my job" guard below.
    if (message.type === "genso.job") { syncJobFromServer(data.payload); return; }
    if (message.type === "genso.form") { syncFormFromServer(data.payload); return; }
    if (!state.currentJob) return;
    if (data.prompt_id && data.prompt_id !== state.currentJob.id) return;
    if (message.type === "executing") {
      if (data.node != null) state.currentJob.stage = STAGE_NAMES[String(data.node)] || `処理 ${data.node}`;
      updateProgressUi();
    } else if (message.type === "progress_state") {
      for (const node of Object.values(data.nodes || {})) {
        const value = Number(node.value);
        const max = Number(node.max);
        if (!value || !max || max <= 1 || value === state.currentJob.lastStep) continue;
        const now = performance.now();
        if (state.currentJob.lastStepAt) {
          state.currentJob.stepTimes.push((now - state.currentJob.lastStepAt) / 1000);
          state.currentJob.stepTimes = state.currentJob.stepTimes.slice(-5);
        }
        state.currentJob.lastStepAt = now;
        state.currentJob.lastStep = value;
        state.currentJob.step = value;
        state.currentJob.total = max;
        state.currentJob.stage = "サンプリング";
        updateProgressUi();
      }
    } else if (message.type === "execution_error") {
      failProgress(data.exception_message || "ComfyUI で実行エラーが発生しました");
    }
  };
  // Events sent while the socket was down are gone, so re-read the shared state
  // on every (re)connect rather than trusting what the window last saw.
  ws.onopen = () => { loadSession(); };
  // A dropped socket may mean the engine died mid-job. Ask the session route on
  // every retry: it either restores the real job or clears a card for a job
  // that no longer exists, so the window never stays stuck on a dead run.
  ws.onclose = () => {
    state.wsRetry = setTimeout(() => { loadSession(); connectWebSocket(); }, 3000);
  };
  ws.onerror = () => ws.close();
}

function syncJobFromServer(job) {
  if (!job) {
    // The engine says nothing is running. Only clear a card we did not already
    // resolve ourselves, so a finished run keeps its "完了" summary on screen.
    if (state.currentJob) {
      state.currentJob = null;
      $("#progressCard").classList.remove("running");
      $("#interruptButton").classList.add("hidden");
    }
    return;
  }
  if (state.currentJob?.id === job.prompt_id) return;
  state.currentJob = {
    id: job.prompt_id,
    started: performance.now() - Math.max(0, (Date.now() / 1000 - (job.started_at || 0))) * 1000,
    stage: job.source === "api" ? "エージェントが実行中" : "実行中",
    step: 0, total: Number(job.steps) || 0, lastStep: 0, lastStepAt: 0, stepTimes: [],
  };
  $("#progressCard").classList.add("running");
  $("#errorGuide").classList.add("hidden");
  $("#interruptButton").classList.remove("hidden");
  updateProgressUi();
  monitorHistory(job.prompt_id);
}

function syncFormFromServer(form) {
  // Ignore the echo of our own submission; the fields already hold these values.
  if (!form || !form.values || form.source === "ui") return;
  applyFormValues(form.values);
  toast("エージェントが入力欄を更新しました");
}

async function describeInput(name) {
  try {
    const info = await requestJson(`/genso/api/probe?name=${encodeURIComponent(name)}`);
    return { name, originalName: name, width: info.width || 0, height: info.height || 0,
             duration: info.duration_sec || 0, fps: info.fps || 0, hasAudio: Boolean(info.has_audio) };
  } catch {
    return { name, originalName: name, width: 0, height: 0, duration: 0, fps: 0, hasAudio: false };
  }
}

async function applyFormValues(values) {
  const uiMode = values.mode === "ref2va" && state.mode === "mannequin" ? "mannequin" : (values.mode || "t2va");
  setMode(uiMode);
  const setValue = (selector, value) => {
    if (value === undefined || value === null) return;
    const node = $(selector);
    if (node) node.value = String(value);
  };
  const setChecked = (selector, value) => {
    if (value === undefined || value === null) return;
    const node = $(selector);
    if (node) node.checked = Boolean(value);
  };

  if (typeof values.prompt === "string") {
    $("#prompt").value = values.prompt;
    state.promptDrafts[state.mode] = values.prompt;
  }
  if (values.width && values.height) {
    $("#customSize").checked = true;
    setValue("#customWidth", values.width);
    setValue("#customHeight", values.height);
  }
  setValue("#steps", values.steps);
  setValue("#seed", values.seed ?? "");
  setValue("#sampler", values.sampler);
  setValue("#scheduler", values.scheduler);
  setValue("#shiftVideo", values.shift_video);
  setValue("#shiftAudio", values.shift_audio);
  setValue("#textEncoder", values.text_encoder);
  setValue("#outName", values.out_name);
  setChecked("#gguf", values.gguf);
  setChecked("#alignAudio", values.align_audio);
  const sizeRadio = $(`input[name=refImageSize][value="${values.ref_image_size || "match"}"]`);
  if (sizeRadio) sizeRadio.checked = true;

  state.firstFrame = values.first_frame ? await describeInput(values.first_frame) : null;
  state.lastFrame = values.last_frame ? await describeInput(values.last_frame) : null;
  renderFrame("first");
  renderFrame("last");

  state.refImages = await Promise.all((values.ref_images || []).map(describeInput));
  const useAudio = values.ref_video_use_audio || [];
  state.refVideos = await Promise.all((values.ref_videos || []).map(async (name, index) => ({
    ...(await describeInput(name)), originalUpload: name, useAudio: Boolean(useAudio[index]),
    targetWidth: values.width, targetHeight: values.height, processing: false,
  })));
  state.refAudios = await Promise.all((values.ref_audios || []).map(describeInput));
  renderReferences();

  // Length last: aligning to the audio depends on the audio list being loaded.
  setValue("#length", values.length);
  alignDurationToAudio();
  updateResolution();
  updateEstimate();
}

async function loadSession() {
  try {
    const snapshot = await requestJson("/genso/api/session");
    if (snapshot.form?.values && snapshot.form.source !== "ui") await applyFormValues(snapshot.form.values);
    syncJobFromServer(snapshot.job);
  } catch (error) {
    console.warn("GENSO session:", error);
    // The engine is not answering. Leaving a job card up would trap the user:
    // stop cannot reach a dead engine, so every action stays blocked behind a
    // job that is no longer running. Release the screen and say why.
    if (state.currentJob) {
      state.currentJob = null;
      $("#progressCard").classList.remove("running");
      $("#interruptButton").classList.add("hidden");
      $("#progressStage").textContent = "エンジンに接続できません";
      toast("エンジンに接続できません。生成状態を解除しました", 8000);
    }
  }
}

function findMp4(value) {
  if (!value || typeof value !== "object") return null;
  if (typeof value.filename === "string" && value.filename.toLowerCase().endsWith(".mp4")) return value.filename;
  for (const child of Array.isArray(value) ? value : Object.values(value)) {
    const found = findMp4(child);
    if (found) return found;
  }
  return null;
}

async function monitorHistory(promptId) {
  while (state.currentJob?.id === promptId) {
    try {
      const history = await requestJson(`/history/${encodeURIComponent(promptId)}`);
      if (history[promptId]) {
        const record = history[promptId];
        if (record.status?.status_str === "success") {
          const filename = findMp4(record.outputs);
          if (!filename) throw new Error("生成は完了しましたが、出力動画名を取得できませんでした");
          await completeProgress(filename);
          return;
        }
        if (record.status?.status_str && record.status.status_str !== "success") {
          const executionError = (record.status.messages || []).find((item) => item[0] === "execution_error");
          failProgress(executionError?.[1]?.exception_message || "生成に失敗しました");
          return;
        }
      }
    } catch (error) {
      // A temporary history/read disconnect must not mark a long job failed.
      console.warn("GENSO history poll:", error);
    }
    await new Promise((resolve) => setTimeout(resolve, 4000));
  }
}

async function completeProgress(filename) {
  const elapsed = state.currentJob ? (performance.now() - state.currentJob.started) / 1000 : 0;
  $("#progressStage").textContent = "完了";
  $("#stepLabel").textContent = "保存済み";
  $("#timeLabel").textContent = `所要 ${formatClock(elapsed)}`;
  $("#progressFill").style.width = "100%";
  $("#progressHalo").style.width = "100%";
  $("#progressCard").classList.remove("running");
  $("#interruptButton").classList.add("hidden");
  reportJobFinished(state.currentJob?.id, "done");
  state.currentJob = null;
  playVideo(filename);
  await runQc(filename);
  await loadHistory();
}

function reportJobFinished(promptId, status) {
  if (!promptId) return;
  // Advisory: the server re-checks with the engine, so a missed report is safe.
  fetch("/genso/api/job_finished", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt_id: promptId, status }),
  }).catch(() => {});
}

function errorGuide(message) {
  const lower = message.toLowerCase();
  if (lower.includes("out of memory") || lower.includes("cuda oom")) return "サイズか尺を下げてやり直してください。他の生成アプリ（Forge 等）が VRAM を使っていないかも確認してください。";
  if (lower.includes("vram") || message.includes("GiB")) return "翻訳直後なら数秒待ち、Ollamaモデルが解放されてから再実行してください。解放後も不足する場合は他の生成アプリを閉じてください。";
  if (message.includes("再起動")) return "エンジンを再起動してから、もう一度実行してください。";
  if (lower.includes("black") || message.includes("真っ黒")) return "真っ黒な動画が生成されました。起動フラグが書き換えられていないか確認してください（fp16 系フラグは必ず破綻します）。";
  return "engine.log と表示されたエラーを確認してください。入力ファイル・モデル配置・サイズ設定も見直してください。";
}

function failProgress(message) {
  reportJobFinished(state.currentJob?.id, "failed");
  state.currentJob = null;
  $("#progressCard").classList.remove("running");
  $("#progressStage").textContent = "生成エラー";
  $("#stepLabel").textContent = "失敗";
  $("#timeLabel").textContent = message;
  $("#progressFill").style.width = "0";
  $("#progressHalo").style.width = "0";
  $("#interruptButton").classList.add("hidden");
  const guide = $("#errorGuide");
  guide.innerHTML = `<strong>${escapeHtml(message)}</strong><br>${escapeHtml(errorGuide(message))}`;
  guide.classList.remove("hidden");
  toast(message, 7000);
}

async function interrupt() {
  const button = $("#interruptButton");
  button.disabled = true;
  const previous = button.textContent;
  button.textContent = "中断中…";
  try {
    // The server confirms the job really left the queue before saying so; a
    // request that returns while the job is still sampling is not a failure,
    // it just means the sampler has not reached a point where it can stop.
    const result = await requestJson("/genso/api/interrupt", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    if (result.stopped) failProgress(result.message);
    else toast(result.message, 10000);
  } catch (error) {
    toast(error.message, 8000);
  } finally {
    button.disabled = false;
    button.textContent = previous;
  }
}

function playVideo(filename) {
  const base = filename.replaceAll("\\", "/").split("/").pop();
  const video = $("#resultVideo");
  video.src = `/genso/video/${encodeURIComponent(base)}`;
  video.classList.remove("hidden");
  $("#playerEmpty").classList.add("hidden");
  video.load();
}

async function runQc(filename) {
  const base = filename.replaceAll("\\", "/").split("/").pop();
  const node = $("#qcResult");
  node.className = "qc-result";
  node.textContent = "QC: 映像と音声を検査中…";
  try {
    const result = await requestJson("/genso/api/qc", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename: base }),
    });
    node.classList.add(result.verdict === "OK" ? "ok" : "warn");
    const sync = result.stats?.reference_sync;
    // Only meaningful when a reference voice was actually supplied.
    const syncNote = sync?.available
      ? `　/　参照音声との一致 r=${sync.correlation}${sync.lag_ms ? `（${sync.lag_ms > 0 ? "+" : ""}${sync.lag_ms}ms ずれ）` : "（ずれなし）"}`
      : "";
    if (result.verdict === "OK") node.textContent = `QC OK · 黒画面・NaN・無音の異常は検出されませんでした${syncNote}`;
    else if (result.verdict === "NOREF") node.textContent = `QC NOREF · 渡した参照音声が使われていません（r=${sync?.correlation}）。参照の配線を確認してください`;
    else if (result.verdict === "SILENT") node.textContent = "QC SILENT · 音声がほぼ無音です。無音指定なら問題ありません";
    else if (result.verdict === "BLACK") node.textContent = "QC BLACK · 真っ黒な動画です。起動フラグを確認してください（fp16 系フラグは破綻します）";
    else node.textContent = `QC ${result.verdict} · 出力に異常の可能性があります。再生して確認してください${syncNote}`;
  } catch (error) {
    node.classList.add("warn");
    node.textContent = `QC を実行できませんでした: ${error.message}`;
  }
}

async function loadHistory() {
  const root = $("#historyGrid");
  try {
    const items = await requestJson("/genso/api/history");
    if (!items.length) {
      root.innerHTML = '<div class="history-empty">まだ生成履歴はありません</div>';
      return;
    }
    root.innerHTML = items.map((item) => {
      const title = item.settings ? escapeHtml(JSON.stringify(item.settings, null, 2)) : "生成条件なし";
      const date = new Date(item.mtime * 1000).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
      return `<article class="history-item" data-history-file="${escapeHtml(item.filename)}" title="${title}"><video preload="metadata" muted src="/genso/video/${encodeURIComponent(item.filename)}#t=0.1"></video><div class="history-info"><strong>${escapeHtml(item.filename)}</strong><small>${date} · ${formatBytes(item.size)}</small></div></article>`;
    }).join("");
    $$('[data-history-file]', root).forEach((item) => item.addEventListener("click", () => playVideo(item.dataset.historyFile)));
  } catch (error) {
    root.innerHTML = `<div class="history-empty">${escapeHtml(error.message)}</div>`;
  }
}

async function restartEngine() {
  if (!window.pywebview?.api?.restart_engine) {
    toast("エンジン再起動は GENSO のデスクトップウィンドウから利用できます");
    return;
  }
  try {
    const external = new URLSearchParams(location.search).get("shell") === "external";
    if (external && !window.confirm("外部で起動された ComfyUI を終了して、GENSO 推奨設定で再起動します。続けますか？")) return;
    $("#restartButton").disabled = true;
    $("#stopButton").disabled = true;
    $("#engineStatus").innerHTML = '<span class="status-dot"></span><span>再起動中</span>';
    $("#engineMeta").textContent = "エンジンが戻ると画面が自動で開き直します（初回は15秒ほど）";
    // Fire and forget, and navigate from nowhere here: restarting kills the
    // server that serves this page, so awaiting a reply cannot work — the
    // pending callback dies with the document and pywebview then crashes the
    // app trying to resolve it. The shell reopens the window itself, with the
    // `shell=owned` query it is now entitled to.
    window.pywebview.api.restart_engine();
  } catch (error) {
    toast(error.message, 7000);
    $("#restartButton").disabled = false;
    $("#stopButton").disabled = false;
  }
}

async function stopEngine() {
  if (!window.pywebview?.api?.stop_engine) {
    toast("エンジン停止は GENSO のデスクトップウィンドウから利用できます");
    return;
  }
  if (state.currentJob && !window.confirm("生成中です。エンジンを止めると結果は失われます。停止しますか？")) return;
  // Same reason as restartEngine: this page is served by the engine being
  // stopped, so there is nothing left to deliver a reply to. The shell shows
  // its own "stopped" page, which can start the engine again.
  state.currentJob = null;
  $("#progressCard").classList.remove("running");
  $("#interruptButton").classList.add("hidden");
  $("#stopButton").disabled = true;
  $("#restartButton").disabled = true;
  $("#engineStatus").className = "engine-pill";
  $("#engineStatus").innerHTML = '<span class="status-dot"></span><span>停止中</span>';
  window.pywebview.api.stop_engine();
}

function refreshFrontend() {
  // Reloading is always safe: the job lives in the engine, not in this window,
  // and loadSession() restores whatever is actually running on the way back up.
  // Blocking it used to be the only escape route from a stale job card, which
  // left no way out when the engine died mid-run.
  const nextUrl = new URL(location.href);
  nextUrl.searchParams.set("ui", Date.now().toString());
  location.replace(nextUrl.toString());
}

function bindDropzone(selector, handler) {
  const zone = $(selector);
  ["dragenter", "dragover"].forEach((eventName) => zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((eventName) => zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.remove("drag"); }));
  zone.addEventListener("drop", (event) => handler(event.dataTransfer.files[0]));
}

function bindEvents() {
  $("#editorForm").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!state.editor) return;
    try {
      const values = readEditorValues();
      state.editor.onSubmit(values);
      closeStructuredEditor();
    } catch (error) {
      toast(error.message, 6000);
    }
  });
  $("#editorClose").addEventListener("click", closeStructuredEditor);
  $("#editorCancel").addEventListener("click", closeStructuredEditor);
  $("#editorOverlay").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeStructuredEditor();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.editor) closeStructuredEditor();
  });
  $$(".mode-tab").forEach((tab) => tab.addEventListener("click", () => setMode(tab.dataset.mode)));
  $("#reloadModels").addEventListener("click", loadStatus);
  $("#refreshButton").addEventListener("click", refreshFrontend);
  $("#restartButton").addEventListener("click", restartEngine);
  $("#stopButton").addEventListener("click", stopEngine);
  $("#openFolder").addEventListener("click", async () => { try { await requestJson("/genso/api/open_folder", { method: "POST" }); } catch (error) { toast(error.message); } });
  $("#reloadHistory").addEventListener("click", loadHistory);
  $("#generateButton").addEventListener("click", generate);
  $("#interruptButton").addEventListener("click", interrupt);

  $("#templateSelect").addEventListener("change", (event) => {
    const template = state.templates.find((item) => item.id === event.target.value);
    if (template) openTemplateEditor(template);
    event.target.value = "";
  });
  $("#translatePrompt").addEventListener("click", translatePrompt);
  $("#prompt").addEventListener("input", (event) => {
    state.promptDrafts[state.mode] = event.target.value;
  });
  $("#insertDialogue").addEventListener("click", insertDialogue);
  $("#insertSilence").addEventListener("click", () => insertAtCursor("Audio: quiet room tone only. No background music, no sound effects, no voices."));
  $("#insertAmbience").addEventListener("click", () => insertAtCursor("Audio: natural environmental ambience only. No background music, no voices."));
  $("#insertDeclaration").addEventListener("click", insertDeclaration);
  $("#insertSubtitle").addEventListener("click", insertSubtitle);
  $("#insertLipSync").addEventListener("click", () => insertAtCursor(LIP_SYNC));
  $("#regenerateMannequin").addEventListener("click", () => refreshMannequinPrompt(true));
  $$("input[name=bodyType]").forEach((input) => input.addEventListener("change", () => refreshMannequinPrompt(true)));

  $("#firstFrameInput").addEventListener("change", (event) => { setFrame("first", event.target.files[0]); event.target.value = ""; });
  $("#lastFrameInput").addEventListener("change", (event) => { setFrame("last", event.target.files[0]); event.target.value = ""; });
  bindDropzone('[data-drop-target="first"]', (file) => setFrame("first", file));
  bindDropzone('[data-drop-target="last"]', (file) => setFrame("last", file));
  $("#loopButton").addEventListener("click", () => {
    if (!state.firstFrame) { toast("先に開始フレームを選択してください"); return; }
    state.lastFrame = { ...state.firstFrame };
    renderFrame("last");
  });
  $("#matchImageSize").addEventListener("click", () => matchFirstImage(true));

  $("#refImageInput").addEventListener("change", (event) => { addReferenceImages(event.target.files); event.target.value = ""; });
  $("#refVideoInput").addEventListener("change", (event) => { addReferenceVideos(event.target.files); event.target.value = ""; });
  $("#refAudioInput").addEventListener("change", (event) => { addReferenceAudios(event.target.files); event.target.value = ""; });
  $("#alignAudio").addEventListener("change", alignDurationToAudio);
  $$("input[name=refImageSize]").forEach((input) => input.addEventListener("change", () => {
    const max = $("input[name=refImageSize]:checked").value === "max";
    $("#maxRefWarning").classList.toggle("hidden", !max);
    updateEstimate();
  }));

  ["#aspectRatio", "#quality", "#length", "#customSize", "#customWidth", "#customHeight"].forEach((selector) => $(selector).addEventListener("change", updateResolution));
  ["#customWidth", "#customHeight", "#steps", "#gguf"].forEach((selector) => $(selector).addEventListener("input", updateEstimate));
}

setInterval(updateProgressUi, 1000);

async function init() {
  bindEvents();
  renderFrame("first");
  renderFrame("last");
  renderReferences();
  updateResolution();
  connectWebSocket();
  detectWrapperState();
  try {
    state.templates = await requestJson("/genso/api/templates");
  } catch (error) { toast(error.message); }
  updateTemplateOptions();
  await Promise.all([loadStatus(), loadHistory()]);
  setMode("t2va");
  // Last, so it can override the default mode: pick up whatever is already in
  // flight or already filled in, whoever put it there.
  await loadSession();
}

init();
