// 로컬 이미지 생성기 프런트엔드 (서버: app/server.py)
"use strict";
const $ = (id) => document.getElementById(id);
let INFO = null;
let family = "anima";
let mode = "t2i";
// 모델마다 설정을 따로 보관 (다른 모델에 같은 값이 일괄 적용되지 않도록)
const perFamily = {};
let slots = []; // [{name,url,source}]
let lastResult = null;
let lastShownJob = null;

function toast(msg, ms = 3500) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), ms);
}
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch { body = null; }
  if (!r.ok) throw new Error((body && body.error) || `요청 실패 (${r.status})`);
  return body;
}
const post = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });
const gb = (b) => (b / 1073741824).toFixed(1);
const fam = () => INFO.families[family];

function presetValues(fid, pid, variantOverride) {
  const f = INFO.families[fid], p = f.presets[pid];
  const variant = variantOverride || p.variant;
  const sz = INFO.size_presets[p.size || "square"];
  const v = f.variants[variant] || {};
  // SD1.5 checkpoints default to 512×768 unless the preset fixes a size
  let w = p.width || sz[1], h = p.height || sz[2];
  if (!p.width && v.default_size) { w = v.default_size[0]; h = v.default_size[1]; }
  if (p.width && v.arch === "sd15") { w = Math.min(w, 512); h = Math.min(h, 512); }
  const neg = typeof f.default_negative === "string" ? f.default_negative
    : ((f.default_negative && f.default_negative[variant]) || "");
  return {
    preset: pid, variant, steps: p.steps, cfg: p.cfg, sampler: p.sampler, scheduler: p.scheduler,
    width: w, height: h, size: (p.width || v.default_size) ? "custom" : (p.size || "square"),
    cache_device: p.cache_device || "auto",
    strength: f.img2img_default_strength || 0.6, inpaint_strength: 1.0,
    negative: neg, loras: [],
  };
}
function stateFor(fid) {
  if (!perFamily[fid]) perFamily[fid] = presetValues(fid, "default");
  return perFamily[fid];
}
function saveForm() {
  if (!INFO) return;
  const s = stateFor(family);
  Object.assign(s, {
    variant: $("variant").value, steps: +$("steps").value, cfg: +$("cfg").value, sampler: $("sampler").value,
    scheduler: $("scheduler").value, width: +$("width").value, height: +$("height").value,
    negative: $("negative").value, cache_device: $("cache-device").value,
  });
  if (mode === "inpaint") s.inpaint_strength = +$("strength").value; else s.strength = +$("strength").value;
  if (fam().lora) s.loras = [...document.querySelectorAll("#lora-rows .lora-row")].map((r) => ({
    name: r.querySelector("select").value, strength: +r.querySelector("input").value,
  })).filter((l) => l.name);
}

// ------------------------------------------------------------------ rendering
function renderFamilies() {
  const box = $("family-list"); box.innerHTML = "";
  for (const [fid, f] of Object.entries(INFO.families)) {
    const b = document.createElement("button");
    b.className = "choice" + (fid === family ? " active" : "");
    const installed = Object.keys(f.variants).some((v) => INFO.installed[v]);
    b.innerHTML = `<b>${f.label}</b>` + (installed ? "" : " <small>(미설치)</small>");
    b.setAttribute("aria-label", `모델 ${f.label}` + (installed ? "" : " (미설치)"));
    b.disabled = !installed;
    b.onclick = () => { saveForm(); family = fid; if (!f.modes[mode].supported) mode = "t2i"; renderAll(); };
    box.appendChild(b);
  }
  $("family-summary").textContent = fam().summary;
}
function renderModes() {
  const box = $("mode-list"); box.innerHTML = "";
  for (const [mid, label] of Object.entries(INFO.modes)) {
    const m = fam().modes[mid];
    const b = document.createElement("button");
    b.className = "choice small" + (mid === mode ? " active" : "");
    b.textContent = label + (m.supported ? "" : " (미지원)");
    b.disabled = !m.supported;
    b.title = m.supported ? (m.note || "") : m.reason;
    b.setAttribute("aria-label", b.textContent);
    b.onclick = () => { mode = mid; renderAll(); };
    box.appendChild(b);
  }
  const m = fam().modes[mode];
  const unsupported = Object.entries(fam().modes).filter(([, v]) => !v.supported)
    .map(([k, v]) => `• ${INFO.modes[k]} 미지원: ${v.reason}`).join("\n");
  $("mode-note").textContent = (m.note || "") + (unsupported ? "\n" + unsupported : "");
  $("mode-note").style.whiteSpace = "pre-wrap";
  const pbox = $("preset-list"); pbox.innerHTML = "";
  const s = stateFor(family);
  for (const [pid, p] of Object.entries(fam().presets)) {
    const b = document.createElement("button");
    b.className = "choice small" + (s.preset === pid ? " active" : "");
    b.textContent = p.label;
    b.disabled = !INFO.installed[p.variant];
    if (b.disabled) b.title = "이 프리셋의 모델 파일이 설치되지 않았습니다.";
    b.onclick = () => { perFamily[family] = presetValues(family, pid); renderAll(); };
    pbox.appendChild(b);
  }
}
function fillSelect(sel, items, value) {
  sel.innerHTML = "";
  for (const it of items) {
    const [v, label] = Array.isArray(it) ? it : [it, it];
    const o = document.createElement("option"); o.value = v; o.textContent = label; sel.appendChild(o);
  }
  if (value !== undefined) sel.value = value;
}
function renderForm() {
  const f = fam(), s = stateFor(family);
  fillSelect($("variant"), Object.entries(f.variants).map(([k, v]) => [k, v.label + (INFO.installed[k] ? "" : " (미설치)")]), s.variant);
  const vinfo = f.variants[s.variant] || { precision: "사용 가능한 체크포인트가 없습니다 (models\\checkpoints에 .safetensors를 넣으세요)", negative: false };
  $("precision").textContent = "정밀도/구성: " + vinfo.precision;
  $("steps").value = s.steps; $("cfg").value = s.cfg;
  fillSelect($("sampler"), INFO.samplers, s.sampler);
  fillSelect($("scheduler"), INFO.schedulers, s.scheduler);
  $("cache-device").value = s.cache_device || "auto";
  $("qwen-opts").classList.toggle("hidden", family !== "qwen21");
  const turboFixed = s.variant === "qwen21_turbo";
  $("steps").disabled = turboFixed; $("scheduler").disabled = turboFixed;
  $("adv-note").textContent = turboFixed ? "Qwen-Image 2.1 Turbo는 공식 8스텝 고정 시그마 스케줄을 사용하므로 스텝/스케줄러를 바꿀 수 없습니다." :
    ((s.variant || "").includes("turbo") ? "Turbo(증류) 모델은 CFG 1.0이 권장값이며 이때 네거티브 프롬프트는 쓰이지 않습니다." : "");
  $("width").value = s.width; $("height").value = s.height;
  $("width").step = f.multiple; $("height").step = f.multiple;
  $("size-note").textContent = `${f.multiple}의 배수로 자동 보정, ${f.min_side}~${f.max_side}px`;
  // size presets
  const sbox = $("size-presets"); sbox.innerHTML = "";
  for (const [k, v] of Object.entries(INFO.size_presets)) {
    const b = document.createElement("button");
    b.className = "choice small" + (s.size === k ? " active" : "");
    b.textContent = `${v[0]} (${v[1]}×${v[2]})`;
    b.onclick = () => {
      s.size = k; s.width = v[1]; s.height = v[2];
      if (k.startsWith("shorts")) $("export").value = "1080x1920";
      else if (k === "landscape") $("export").value = "1920x1080";
      renderForm();
    };
    sbox.appendChild(b);
  }
  // negative
  const negOk = vinfo.negative;
  const negActive = negOk && +s.cfg > 1;
  $("negative").value = s.negative || "";
  $("negative").disabled = !negActive;
  $("neg-wrap").classList.toggle("hidden", !negOk);
  $("neg-state").textContent = negOk ? (negActive ? "(사용됨)" : "(CFG가 1보다 커야 적용됩니다)") : "";
  // anima
  $("anima-opts").classList.toggle("hidden", family !== "anima");
  if (family === "anima") fillSelect($("rating-tag"), f.rating_tags.map((t) => [t, t || "지정 안 함"]), $("rating-tag").value || "");
  $("prompt-help").textContent = f.prompt_help;
  $("translate").disabled = $("enhance").disabled = !INFO.textgen_available;
  // reference area
  const m = f.modes[mode];
  $("ref-card").classList.toggle("hidden", mode === "t2i");
  const maxImgs = m.max_images || 1;
  slots = slots.slice(0, maxImgs);
  $("strength-wrap").classList.toggle("hidden", !(mode === "img2img" || mode === "inpaint"));
  $("refstrength-wrap").classList.toggle("hidden", !(mode === "reference" && family === "krea2"));
  const st = mode === "inpaint" ? (s.inpaint_strength ?? 1.0) : s.strength;
  $("strength").value = st; $("strength-val").textContent = (+st).toFixed(2);
  $("ref-strength-val").textContent = (+$("ref-strength").value).toFixed(2);
  $("ref-help").textContent = mode === "img2img" ? "입력 이미지 1장을 지정한 크기에 맞게 중앙 기준으로 잘라 변환합니다." :
    mode === "inpaint" ? "이미지 1장을 올린 뒤, 아래 미리보기 위에 고칠 부분을 브러시로 칠하세요. 칠한 곳만 프롬프트대로 다시 그립니다." :
    mode === "edit" ? `최대 ${maxImgs}장. 1번이 편집 대상, 나머지는 참조입니다. 지시문에서 <image1>, <image2>로 지칭하세요. 결과 크기는 1번 이미지를 따릅니다.` :
    `최대 ${maxImgs}장의 참조 이미지를 사용합니다.`;
  // in edit mode the canvas follows image_1, so manual size is ignored
  const sizeLocked = mode === "edit" || mode === "inpaint";
  $("width").disabled = $("height").disabled = sizeLocked;
  if (sizeLocked) $("size-note").textContent = mode === "inpaint" ? "부분 수정은 입력 이미지 비율을 따릅니다(약 1MP, SD1.5는 약 0.4MP로 조정)."
    : "지시문 편집은 1번 이미지 크기를 따릅니다(약 1MP로 조정).";
  renderSlots(maxImgs);
  renderLoras();
}

// ------------------------------------------------------------------ LoRA (every family; models_config "lora")
const ARCH_NAME = { sd15: "SD1.5", sd3: "SD3.x", sd35_medium: "SD3.5 Medium", sd35_large: "SD3.5 Large", sdxl: "SDXL", anima: "Anima", krea2: "Krea 2",
                    qwen21: "Qwen-Image 2.1", moss: "MOSS-Image", qwen_image: "Qwen-Image 1.x", flux: "Flux.1" };
function renderLoras() {
  const f = fam(), s = stateFor(family);
  $("lora-card").classList.toggle("hidden", !f.lora);
  if (!f.lora) return;
  const v = f.variants[s.variant] || {};
  // only LoRAs of the selected model's architecture (unknown ones are always shown); a variant's own
  // "lora_archs" (SD 3.5 Medium vs Large, SDXL vs SD1.5 checkpoint) narrows the family list
  const va = v.lora_archs || (v.arch ? [v.arch] : null);
  const list = (f.loras || []).filter((l) => l.arch === "unknown" || !va || va.includes(l.arch));
  const archName = (va && ARCH_NAME[va[0]]) || (f.lora_archs || []).map((a) => ARCH_NAME[a] || a).join("·") || f.label;
  $("lora-help").textContent = (list.length
    ? `models\\loras 폴더에서 이 모델(${archName})용 LoRA ${list.length}개를 찾았습니다(최대 3개). `
    : `models\\loras 폴더에 ${archName}용 LoRA(.safetensors)를 넣으면 여기에 나타납니다(새로고침 필요). `)
    + (f.lora_help || "") + " 계열을 판별하지 못한 LoRA는 모든 모델에 보이며, 맞지 않으면 효과 없이 무시됩니다.";
  const box = $("lora-rows"); box.innerHTML = "";
  // drop picks that do not fit the selected variant (e.g. a Large LoRA after switching to SD 3.5 Medium)
  if (s.loras) s.loras = s.loras.filter((lo) => list.some((l) => l.name === lo.name));
  const rows = (s.loras || []).concat([{ name: "", strength: 1.0 }]).slice(0, 3);
  rows.forEach((lo, i) => {
    const row = document.createElement("div"); row.className = "row lora-row";
    const sel = document.createElement("select");
    sel.setAttribute("aria-label", `LoRA ${i + 1}`);
    fillSelect(sel, [["", "LoRA 사용 안 함"]].concat(list.map((l) => [l.name, `${l.name} (${l.arch_label}, ${l.size_gb}GB)`])), lo.name);
    const inp = document.createElement("input"); inp.type = "number"; inp.step = "0.05"; inp.min = "-2"; inp.max = "2";
    inp.value = lo.strength; inp.title = "LoRA 강도"; inp.setAttribute("aria-label", `LoRA ${i + 1} 강도`);
    sel.onchange = () => { saveForm(); renderLoras(); };
    inp.onchange = () => saveForm();
    row.append(sel, Object.assign(document.createElement("span"), { textContent: "강도", className: "hint" }), inp);
    box.appendChild(row);
  });
}

// ------------------------------------------------------------------ inpaint mask editor
const mask = { drawing: false, erase: false, size: 40, hasPaint: false, src: null };
function renderMaskEditor() {
  const show = mode === "inpaint" && !!slots[0];
  $("mask-editor").classList.toggle("hidden", !show);
  if (!show) return;
  if (mask.src === slots[0].url) return;           // same image: keep painted strokes
  mask.src = slots[0].url; mask.hasPaint = false;
  const img = $("mask-base"), cv = $("mask-canvas");
  img.onload = () => { cv.width = img.naturalWidth; cv.height = img.naturalHeight; cv.getContext("2d").clearRect(0, 0, cv.width, cv.height); };
  img.src = slots[0].url;
}
function maskPos(e) {
  const cv = $("mask-canvas"), r = cv.getBoundingClientRect();
  return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height, cv.width / r.width];
}
function maskStroke(e, start) {
  const cv = $("mask-canvas"), ctx = cv.getContext("2d");
  const [x, y, k] = maskPos(e);
  ctx.globalCompositeOperation = mask.erase ? "destination-out" : "source-over";
  ctx.strokeStyle = "rgba(255,40,90,1)"; ctx.fillStyle = "rgba(255,40,90,1)";
  ctx.lineWidth = mask.size * k; ctx.lineCap = "round"; ctx.lineJoin = "round";
  if (start) { ctx.beginPath(); ctx.arc(x, y, ctx.lineWidth / 2, 0, Math.PI * 2); ctx.fill(); ctx.beginPath(); ctx.moveTo(x, y); }
  else { ctx.lineTo(x, y); ctx.stroke(); }
  if (!mask.erase) mask.hasPaint = true;
}
function initMaskEditor() {
  const cv = $("mask-canvas");
  cv.addEventListener("pointerdown", (e) => { mask.drawing = true; cv.setPointerCapture(e.pointerId); maskStroke(e, true); });
  cv.addEventListener("pointermove", (e) => { if (mask.drawing) maskStroke(e, false); });
  const end = () => { mask.drawing = false; };
  cv.addEventListener("pointerup", end); cv.addEventListener("pointercancel", end);
  $("mask-size").oninput = () => { mask.size = +$("mask-size").value; $("mask-size-val").textContent = mask.size; };
  $("mask-brush").onclick = () => { mask.erase = false; $("mask-brush").classList.add("active"); $("mask-eraser").classList.remove("active"); };
  $("mask-eraser").onclick = () => { mask.erase = true; $("mask-eraser").classList.add("active"); $("mask-brush").classList.remove("active"); };
  $("mask-clear").onclick = () => { cv.getContext("2d").clearRect(0, 0, cv.width, cv.height); mask.hasPaint = false; };
}
async function uploadMask() {
  if (!mask.hasPaint) throw new Error("고칠 영역을 이미지 위에 브러시로 칠해 주세요.");
  const r = await post("/api/upload_mask", { data: $("mask-canvas").toDataURL("image/png") });
  return r.name;
}
function renderSlots(maxImgs) {
  const box = $("slots"); box.innerHTML = "";
  for (let i = 0; i < maxImgs; i++) {
    const sl = slots[i];
    const d = document.createElement("div");
    d.className = "slot";
    d.innerHTML = sl ? `<img src="${sl.url}"><span class="label">${i + 1}번 · ${sl.width}×${sl.height}</span><button class="x" title="제거">✕</button>`
      : `<span>➕</span><span class="label">${i + 1}번 이미지<br>클릭 또는 끌어다 놓기</span>`;
    const input = document.createElement("input");
    input.type = "file"; input.accept = "image/*"; input.className = "hidden";
    input.onchange = () => input.files[0] && uploadFile(input.files[0], i);
    d.appendChild(input);
    d.onclick = (e) => {
      if (e.target.classList.contains("x")) { slots.splice(i, 1); renderSlots(maxImgs); renderMaskEditor(); e.stopPropagation(); return; }
      input.click();
    };
    d.ondragover = (e) => { e.preventDefault(); d.classList.add("drag"); };
    d.ondragleave = () => d.classList.remove("drag");
    d.ondrop = (e) => {
      e.preventDefault(); d.classList.remove("drag");
      const f = e.dataTransfer.files[0];
      if (f) uploadFile(f, i);
    };
    box.appendChild(d);
  }
}
async function uploadFile(file, idx) {
  const fd = new FormData(); fd.append("file", file, file.name);
  try {
    const r = await api("/api/upload", { method: "POST", body: fd });
    setSlot(idx, r); toast(`이미지를 불러왔습니다: ${file.name}`);
  } catch (e) { toast(e.message); }
}
function setSlot(idx, r) {
  const maxImgs = fam().modes[mode].max_images || 1;
  idx = Math.min(idx, slots.length, maxImgs - 1);
  slots[idx] = r; renderSlots(maxImgs); renderMaskEditor();
}
function renderAll() { renderFamilies(); renderModes(); renderForm(); renderMaskEditor(); }

// ------------------------------------------------------------------ generate
function collect() {
  saveForm();
  const s = stateFor(family);
  return {
    family, mode, variant: s.variant, preset: s.preset, prompt: $("prompt").value, negative: $("negative").value,
    width: s.width, height: s.height, steps: s.steps, cfg: s.cfg, sampler: s.sampler, scheduler: s.scheduler,
    seed: $("seed").value === "" ? null : +$("seed").value, random_seed: $("random-seed").checked,
    images: mode === "t2i" ? [] : slots.filter(Boolean).map((x) => x.name),
    strength: mode === "inpaint" ? s.inpaint_strength : s.strength, ref_strength: +$("ref-strength").value, cache_device: s.cache_device,
    loras: fam().lora ? (s.loras || []) : [],
    translate: $("translate").checked, enhance: $("enhance").checked,
    quality_prefix: $("quality-prefix").checked, rating_tag: $("rating-tag").value || "",
    export: $("export").value, count: +$("count").value || 1,
  };
}
async function generate() {
  $("form-error").textContent = "";
  try {
    const payload = collect();
    if (mode === "inpaint") payload.mask = await uploadMask();
    const r = await post("/api/generate", payload);
    toast(`${r.jobs.length}건을 대기열에 추가했습니다.`);
    lastShownJob = null;
  } catch (e) { $("form-error").textContent = e.message; }
}

// ------------------------------------------------------------------ status polling
const STATUS_KO = { queued: "대기", running: "실행 중", done: "완료", error: "오류", cancelled: "취소됨" };
async function poll() {
  try {
    const st = await api("/api/status");
    const eng = $("st-engine");
    eng.textContent = { ready: "엔진 준비됨", starting: "엔진 시작 중…", error: "엔진 오류", stopped: "엔진 꺼짐" }[st.engine] || st.engine;
    eng.className = "pill " + (st.engine === "ready" ? "ok" : st.engine === "starting" ? "busy" : "bad");
    eng.title = st.engine_error || "";
    $("st-loaded").textContent = "로드된 모델: " + (st.loaded_family ? INFO.families[st.loaded_family].label : "없음");
    const m = st.mem;
    $("st-vram").textContent = `VRAM ${(m.vram_used / 1024).toFixed(1)}/${(m.vram_total / 1024).toFixed(1)}GB · GPU ${m.gpu_util}%`;
    $("st-ram").textContent = `RAM ${gb(m.ram_used)}/${gb(m.ram_total)}GB (엔진 ${gb(m.comfy_rss)}GB)`;
    $("st-ram").className = "pill" + (m.ram_avail < 3 * 1073741824 ? " bad" : "");
    $("st-queue").textContent = `대기열 ${st.queue.length}`;
    const cur = st.current;
    if (cur) {
      // stall hint: same phase/progress for 3+ minutes
      const key = `${cur.id}|${cur.phase}|${cur.progress}`;
      if (key !== poll.lastKey) { poll.lastKey = key; poll.since = Date.now(); }
      const stalled = Date.now() - poll.since > 180000;
      $("job-phase").textContent = cur.phase + (stalled
        ? "  ⚠ 3분 넘게 진행이 없습니다. 계속 멈춰 있으면 아래 '생성 엔진 재시작'을 누르세요(작업은 취소됩니다)." : "");
      $("job-bar").style.width = cur.progress_max ? (100 * cur.progress / cur.progress_max) + "%" : "5%";
    } else {
      $("job-phase").textContent = st.engine_error ? st.engine_error : "대기 중";
      $("job-bar").style.width = "0";
    }
    const ul = $("queue-list"); ul.innerHTML = "";
    const rows = [...st.queue, ...st.recent.filter((j) => j.status !== "queued")].slice(0, 10);
    for (const j of rows) {
      const li = document.createElement("li");
      const label = `${INFO.families[j.family]?.label ?? j.family} · ${INFO.modes[j.mode] ?? j.mode} · ${j.prompt}`;
      li.innerHTML = `<span>${escapeHtml(label)}</span><span class="st-${j.status}">${STATUS_KO[j.status] || j.status}</span>`;
      if (j.status === "queued" || j.status === "running") {
        const b = document.createElement("button"); b.className = "btn tiny danger"; b.textContent = "취소";
        b.onclick = async () => toast((await post(`/api/cancel/${j.id}`)).message);
        li.appendChild(b);
      }
      if (j.status === "error") li.title = j.error;
      ul.appendChild(li);
    }
    // newest finished job → show
    const fin = st.recent.find((j) => j.status === "done" || j.status === "error" || j.status === "cancelled");
    if (fin && fin.id !== lastShownJob) {
      lastShownJob = fin.id;
      if (fin.status === "done" && fin.result) { showResult(fin.result); loadHistory(0); }  // 새 결과는 1페이지 맨 앞
      else if (fin.status === "error") { $("form-error").textContent = "오류: " + fin.error; }
      else if (fin.status === "cancelled") { toast("작업이 취소되었습니다."); }
    }
    $("cancel-current").disabled = !cur;
  } catch (e) {
    $("st-engine").textContent = "앱 서버 연결 끊김"; $("st-engine").className = "pill bad";
  }
}
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function showResult(rec) {
  lastResult = rec;
  $("result-empty").classList.add("hidden");
  $("result-img").classList.remove("hidden");
  $("result-img").src = `/api/image/${rec.id}?t=${Date.now()}`;
  $("result-actions").classList.remove("hidden");
  $("btn-save").href = `/api/image/${rec.id}`;
  $("btn-save").download = rec.path.split(/[\\/]/).pop();
  const t = rec.timings || {}, pk = rec.peak || {};
  // 지금 앱에 없는 모델/기능으로 만든 예전 기록: 다시 불러오기 없이 기본 정보만 표시
  const known = !!INFO.families[rec.family];
  $("btn-reuse").classList.toggle("hidden", !known);
  if (!known) {
    $("result-info").innerHTML = [
      ["방식", `${rec.family_label || rec.family || "-"} · ${rec.mode_label || rec.mode || "-"} (이전 기록)`],
      ["크기", (rec.final_size || []).join("×") || "-"],
      ["시간", `총 ${t.total_s ?? "-"}s`],
      ["저장 위치", rec.path],
    ].map(([k, v]) => `<tr><td>${k}</td><td>${escapeHtml(v)}</td></tr>`).join("");
    return;
  }
  const rows = [
    ["모델", `${rec.family_label} · ${rec.variant_label}`], ["정밀도/구성", rec.precision],
    ["방식", rec.mode_label + (rec.strength != null ? ` (변환 강도 ${rec.strength})` : "") + (rec.ref_strength != null ? ` (참조 강도 ${rec.ref_strength})` : "")],
    ["크기", `요청 ${rec.requested_size.join("×")} → 생성 ${rec.gen_size.join("×")} → 최종 ${rec.final_size.join("×")}`],
    ["스텝/CFG", `${rec.steps} / ${rec.cfg} · ${rec.sampler} · ${rec.scheduler}`], ["시드", rec.seed],
    ["원본 프롬프트", rec.prompt], ["모델에 전달된 프롬프트", rec.final_prompt + (rec.prompt_processing.length ? `  [${rec.prompt_processing.join("+")} 적용]` : "")],
    ["네거티브(전달됨)", rec.final_negative || "(사용 안 함)"],
    ["시간", `총 ${t.total_s}s · 준비(로딩+인코딩) ${t.prepare_s ?? "-"}s · 샘플링 ${t.sampling_s ?? "-"}s · 디코딩/저장 ${t.decode_save_s ?? "-"}s` +
      (t.avg_step_s ? ` · 스텝당 ${t.avg_step_s}s` : "") + (t.prompt_llm_s ? ` · 프롬프트 처리 ${t.prompt_llm_s}s` : "")],
    ["최대 메모리", `GPU 전체 ${pk.vram_mib}MiB · 엔진 RAM ${pk.comfy_rss_gb}GB · 시스템 최소 여유 ${pk.min_sys_avail_gb}GB`],
    ["이미지 검사", rec.quality_check.suspicious ? "⚠ 단색/검은 이미지가 의심됩니다" : `정상 (밝기 평균 ${rec.quality_check.mean.join("/")})`],
    ["저장 위치", rec.path + (rec.gen_path ? `\n(생성 원본: ${rec.gen_path})` : "")],
  ];
  if (rec.model_switch) rows.push(["모델 전환", rec.model_switch]);
  $("result-info").innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${escapeHtml(v)}</td></tr>`).join("");
}
// 생성 기록 페이지 나누기: 서버에서 한 페이지씩 받아 온다 (offset/limit → {items,total})
const HIST_SIZES = [12, 24, 48, 96];
const hist = { page: 0, size: 24, total: 0 };
try { const v = +localStorage.getItem("historyPageSize"); if (v >= 1 && v <= 500) hist.size = Math.round(v); } catch { /* storage blocked: default */ }
function histPages() { return Math.max(1, Math.ceil(hist.total / hist.size)); }
// 선택 삭제: 고른 기록 id (페이지를 넘겨도 유지)
const histSel = new Set();
function renderHistorySelection() {
  const n = histSel.size;
  $("hist-delete-selected").textContent = `선택 삭제 (${n})`;
  $("hist-delete-selected").disabled = !n;
  $("hist-select-clear").disabled = !n;
  $("hist-sel-count").textContent = n ? `${n}건 선택됨 (다른 페이지 포함)` : "다른 페이지로 넘겨도 선택이 유지됩니다.";
  const cards = [...$("history").querySelectorAll(".h")];
  $("hist-select-page").disabled = !cards.length || cards.every((c) => histSel.has(c.dataset.id));
}
function setHistoryCardsSelected() {  // 화면의 카드 체크 상태를 histSel에 맞춘다
  for (const c of $("history").querySelectorAll(".h")) {
    const on = histSel.has(c.dataset.id);
    c.classList.toggle("sel", on);
    const cb = c.querySelector(".pick input"); if (cb) cb.checked = on;
  }
  renderHistorySelection();
}
// 기록 한 건 = 카드 하나. data-id로 기록 id를 들고 있어 다른 기능(선택 등)이 찾을 수 있다.
function renderHistoryItem(r) {
  const d = document.createElement("div"); d.className = "h"; d.dataset.id = r.id;
  d.innerHTML = `<img loading="lazy" src="/api/image/${r.id}"><div>${r.family_label} · ${r.mode_label}<br>${r.time.slice(5, 16)}</div>`;
  // image file deleted outside the app (e.g. in Explorer): show a placeholder instead of a broken image
  d.querySelector("img").onerror = (e) => { e.target.replaceWith(Object.assign(document.createElement("div"), { className: "missing", textContent: "파일 없음" })); };
  d.title = r.prompt;
  d.onclick = () => showResult(r);
  // 여러 개 골라 삭제용 체크박스 (카드 클릭 = 결과 보기와 분리)
  const pick = document.createElement("label"); pick.className = "pick"; pick.title = "선택";
  const cb = document.createElement("input"); cb.type = "checkbox";
  cb.checked = histSel.has(r.id); d.classList.toggle("sel", cb.checked);
  cb.setAttribute("aria-label", `기록 선택 ${r.family_label} ${r.time}`);
  pick.onclick = (e) => e.stopPropagation();
  cb.onchange = () => {
    if (cb.checked) histSel.add(r.id); else histSel.delete(r.id);
    d.classList.toggle("sel", cb.checked);
    renderHistorySelection();
  };
  pick.appendChild(cb); d.appendChild(pick);
  const del = document.createElement("button");
  del.className = "del"; del.textContent = "✕";
  del.title = "이 기록 삭제"; del.setAttribute("aria-label", `기록 삭제 ${r.family_label} ${r.time}`);
  del.onclick = (e) => { e.stopPropagation(); deleteHistory([r.id]); };
  d.appendChild(del);
  return d;
}
function renderHistoryPager() {
  const pages = histPages(), p = hist.page;
  $("history-pager").classList.toggle("hidden", hist.total <= hist.size);
  $("hist-first").disabled = $("hist-prev").disabled = p <= 0;
  $("hist-next").disabled = $("hist-last").disabled = p >= pages - 1;
  $("hist-page").value = p + 1; $("hist-page").max = pages;
  $("hist-pages").textContent = `/ ${pages} 페이지`;
  $("hist-total").textContent = `(전체 ${hist.total}건)`;
}
// page: 이동할 페이지(0부터). 생략하면 현재 페이지를 다시 읽는다. 범위를 벗어나면 마지막 페이지로 맞춘다.
async function loadHistory(page = hist.page) {
  hist.page = Math.max(0, Math.floor(+page || 0));
  let h = await api(`/api/history?offset=${hist.page * hist.size}&limit=${hist.size}`);
  hist.total = h.total;
  if (hist.page > 0 && hist.page >= histPages()) {  // 삭제 등으로 페이지가 사라짐 → 마지막 페이지로
    hist.page = histPages() - 1;
    h = await api(`/api/history?offset=${hist.page * hist.size}&limit=${hist.size}`);
    hist.total = h.total;
  }
  const box = $("history"); box.innerHTML = "";
  for (const r of h.items) box.appendChild(renderHistoryItem(r));
  if (!h.items.length) box.innerHTML = '<div class="hint">생성 기록이 없습니다.</div>';
  renderHistoryPager();
  renderHistorySelection();
}
function initHistoryPager() {
  const sel = $("hist-size");
  const sizes = HIST_SIZES.includes(hist.size) ? HIST_SIZES : [...HIST_SIZES, hist.size].sort((a, b) => a - b);
  sel.innerHTML = sizes.map((n) => `<option value="${n}">${n}개씩</option>`).join("");
  sel.value = hist.size;
  sel.onchange = () => {
    const first = hist.page * hist.size;  // 보던 첫 항목이 들어 있는 페이지로 이동
    hist.size = +sel.value;
    try { localStorage.setItem("historyPageSize", String(hist.size)); } catch { /* storage blocked: keep for this page only */ }
    loadHistory(Math.floor(first / hist.size));
  };
  $("hist-first").onclick = () => loadHistory(0);
  $("hist-prev").onclick = () => loadHistory(hist.page - 1);
  $("hist-next").onclick = () => loadHistory(hist.page + 1);
  $("hist-last").onclick = () => loadHistory(histPages() - 1);
  $("hist-page").onchange = (e) => {
    const n = Math.min(histPages(), Math.max(1, Math.round(+e.target.value) || 1));
    loadHistory(n - 1);
  };
  $("hist-select-page").onclick = () => {
    for (const c of $("history").querySelectorAll(".h")) histSel.add(c.dataset.id);
    setHistoryCardsSelected();
  };
  $("hist-select-clear").onclick = () => { histSel.clear(); setHistoryCardsSelected(); };
  $("hist-delete-selected").onclick = () => { if (histSel.size) deleteHistory([...histSel]); };
}
// 기록 삭제: 브라우저 기본 confirm 대신 페이지 안의 확인 창 사용
function askConfirm(text) {
  return new Promise((resolve) => {
    $("confirm-text").textContent = text;
    $("confirm-modal").classList.remove("hidden");
    const done = (v) => { $("confirm-modal").classList.add("hidden"); $("confirm-yes").onclick = $("confirm-no").onclick = null; resolve(v); };
    $("confirm-yes").onclick = () => done(true);
    $("confirm-no").onclick = () => done(false);
    $("confirm-no").focus();
  });
}
async function deleteHistory(ids) {
  const all = ids === null;
  const files = $("history-delete-files").checked;
  const what = all ? "생성 기록 전체" : `선택한 생성 기록 ${ids.length}건`;
  const msg = `${what}${all ? "를" : "을"} 삭제할까요?\n` + (files
    ? "결과 이미지와 입력 사본 이미지도 휴지통으로 이동합니다(휴지통에서 복원 가능)."
    : "기록만 지우고 이미지 파일은 출력 폴더에 그대로 남깁니다.");
  if (!(await askConfirm(msg))) return;
  try {
    const r = await post("/api/history/delete", all ? { all: true, delete_files: files } : { ids, delete_files: files });
    toast(`기록 ${r.deleted}건 삭제` + (files ? `, 파일 ${r.files_recycled}개 휴지통 이동` : "") + ` (남은 기록 ${r.remaining}건)`);
    if (lastResult && r.ids.includes(lastResult.id)) clearResult();
    if (all) histSel.clear(); else for (const id of [...ids, ...r.ids]) histSel.delete(id);  // 지운(또는 이미 없던) id는 선택에서 뺀다
    loadHistory();
  } catch (e) { toast(e.message); }
}
function clearResult() {
  lastResult = null;
  $("result-img").classList.add("hidden"); $("result-img").removeAttribute("src");
  $("result-actions").classList.add("hidden"); $("result-info").innerHTML = "";
  $("result-empty").classList.remove("hidden");
}
function reuse(rec) {
  const p = rec.params;
  saveForm();
  family = p.family; mode = p.mode;
  const s = stateFor(family);
  Object.assign(s, { variant: p.variant, steps: p.steps, cfg: p.cfg, sampler: p.sampler, scheduler: p.scheduler,
    width: p.req_width, height: p.req_height, size: "custom", strength: p.strength, negative: p.negative,
    cache_device: p.cache_device || "auto", preset: p.preset, loras: p.loras || [] });
  if (p.mode === "inpaint") s.inpaint_strength = p.strength;
  $("prompt").value = p.prompt; $("seed").value = p.seed; $("random-seed").checked = false;
  $("translate").checked = p.translate; $("enhance").checked = p.enhance;
  $("quality-prefix").checked = p.quality_prefix; $("export").value = p.export;
  $("ref-strength").value = p.ref_strength || 1;
  slots = (p.images || []).map((n) => ({ name: n, url: `/api/input/${n}`, width: "?", height: "?" }));
  renderAll();
  if (family === "anima") $("rating-tag").value = p.rating_tag || "";
  toast(p.mode === "inpaint" ? "이전 설정을 불러왔습니다 (시드 고정). 고칠 영역은 다시 칠해 주세요." : "이전 설정을 불러왔습니다 (시드 고정).");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ------------------------------------------------------------------ tag autocomplete (prompt / negative)
// 커서 앞의 태그 조각(쉼표·줄바꿈 기준)으로 /api/tags를 검색해 힌트를 띄우고, Tab·Enter·클릭으로 태그를 넣는다.
const tagAc = { box: null, input: null, items: [], sel: 0, start: 0, end: 0, timer: 0, seq: 0, enabled: true };
const TAG_SEP = /[,\n]/;
function tagAcLoadPref() {
  try { tagAc.enabled = localStorage.getItem("tagAutocomplete") !== "off"; } catch { tagAc.enabled = true; }
  $("tag-ac").checked = tagAc.enabled;
}
function tagToken(el) {
  const v = el.value, caret = el.selectionStart;
  if (caret !== el.selectionEnd) return null;
  let s = caret; while (s > 0 && !TAG_SEP.test(v[s - 1])) s--;
  let e = caret; while (e < v.length && !TAG_SEP.test(v[e])) e++;
  // keep leading spaces and weight brackets like "(" / "[" outside of the replaced part
  const lead = v.slice(s, caret).match(/^[\s([{]*/)[0].length;
  s += lead;
  // a weight suffix such as ":1.2)" after the caret stays as is
  const tail = v.slice(caret, e).match(/^[^:)\]}]*/)[0];
  e = caret + tail.replace(/\s+$/, "").length;
  const word = v.slice(s, caret);
  return { start: s, end: e, word };
}
function tagCaretPos(el, pos) {
  // mirror div with the textarea's text up to the caret → caret pixel position
  const cs = getComputedStyle(el), m = document.createElement("div");
  for (const p of ["boxSizing", "width", "fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing",
    "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "borderTopWidth", "borderLeftWidth", "borderRightWidth", "wordSpacing", "tabSize"]) m.style[p] = cs[p];
  Object.assign(m.style, { position: "absolute", visibility: "hidden", whiteSpace: "pre-wrap", overflowWrap: "break-word", top: "0", left: "-9999px" });
  m.textContent = el.value.slice(0, pos);
  const mark = document.createElement("span"); mark.textContent = "​"; m.appendChild(mark);
  document.body.appendChild(m);
  const x = mark.offsetLeft, y = mark.offsetTop + mark.offsetHeight;
  m.remove();
  return { x: x - el.scrollLeft, y: y - el.scrollTop };
}
// The server returns prompt-ready tags: \( \) escaped (ComfyUI reads bare () as weights), and for Anima
// artist tags already prefixed with @ (danbooru-tags-comfyui / -anima CSVs).
function tagFormat(it) { return it.tag; }
function tagAcHide() { tagAc.items = []; if (tagAc.box) tagAc.box.classList.add("hidden"); }
function tagAcRender() {
  const box = tagAc.box, el = tagAc.input;
  if (!tagAc.items.length) { tagAcHide(); return; }
  box.innerHTML = "";
  tagAc.items.forEach((it, i) => {
    const d = document.createElement("div");
    d.className = "tag-item" + (i === tagAc.sel ? " sel" : "");
    d.setAttribute("role", "option");
    d.title = it.description;
    const ko = it.korean && it.korean.toLowerCase() !== it.tag ? it.korean : "";
    const cnt = it.count >= 1e6 ? (it.count / 1e6).toFixed(1) + "M" : it.count >= 1e3 ? (it.count / 1e3).toFixed(1) + "k" : String(it.count);
    d.innerHTML = `<span class="tag-name">${escapeHtml(tagFormat(it))}</span>` +
      `<span class="tag-ko">${escapeHtml(ko)}${it.matched_alias ? ` <i>(${escapeHtml(it.matched_alias)})</i>` : ""}</span>` +
      `<span class="tag-cat c${it.category}">${it.category_label}</span><span class="tag-cnt">${cnt}</span>`;
    d.onmousedown = (e) => { e.preventDefault(); tagAc.sel = i; tagAcAccept(); };   // mousedown keeps textarea focus
    box.appendChild(d);
  });
  const r = el.getBoundingClientRect(), c = tagCaretPos(el, tagAc.start);
  box.classList.remove("hidden");
  const left = Math.max(4, Math.min(r.left + window.scrollX + c.x, window.scrollX + document.documentElement.clientWidth - box.offsetWidth - 4));
  let top = r.top + window.scrollY + Math.min(c.y, el.clientHeight) + 2;
  // flip above the caret when there is no room below
  if (r.top + Math.min(c.y, el.clientHeight) + box.offsetHeight > window.innerHeight - 70) top = r.top + window.scrollY + Math.max(0, c.y - parseFloat(getComputedStyle(el).lineHeight || 20)) - box.offsetHeight - 2;
  box.style.left = left + "px"; box.style.top = top + "px";
  box.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
}
function tagAcQuery(el) {
  clearTimeout(tagAc.timer);
  if (!tagAc.enabled || el.disabled) { tagAcHide(); return; }
  const tok = tagToken(el);
  const w = tok && tok.word.trim();
  // ASCII needs 2+ chars; one Korean syllable is already specific enough
  if (!w || (w.length < 2 && /^[\x00-\x7f]$/.test(w))) { tagAcHide(); return; }
  tagAc.timer = setTimeout(async () => {
    const seq = ++tagAc.seq;
    try {
      const r = await api(`/api/tags?limit=12&family=${encodeURIComponent(family)}&q=${encodeURIComponent(w)}`);
      if (seq !== tagAc.seq || document.activeElement !== el) return;   // a newer keystroke won
      tagAc.input = el; tagAc.start = tok.start; tagAc.end = tok.end;
      tagAc.items = r.results; tagAc.sel = 0;
      tagAcRender();
    } catch { tagAcHide(); }
  }, 110);
}
function tagAcAccept() {
  const el = tagAc.input, it = tagAc.items[tagAc.sel];
  if (!el || !it) return;
  const tok = tagToken(el) || { start: tagAc.start, end: tagAc.end };
  const v = el.value, after = v.slice(tok.end);
  const text = tagFormat(it);
  // add ", " unless a separator / weight closer already follows
  const suffix = /^\s*[,:)\]}]/.test(after) ? "" : ", ";
  el.focus();
  el.setSelectionRange(tok.start, tok.end);
  // execCommand keeps the browser undo history (Ctrl+Z); fall back to setRangeText
  if (!document.execCommand("insertText", false, text + suffix)) {
    el.setRangeText(text + suffix, tok.start, tok.end, "end");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  if (!suffix) { const m = el.value.slice(el.selectionStart).match(/^\s*,\s?/); if (m) el.setSelectionRange(el.selectionStart + m[0].length, el.selectionStart + m[0].length); }
  el.dispatchEvent(new Event("change", { bubbles: true }));
  tagAcHide();
}
function initTagAutocomplete() {
  tagAc.box = Object.assign(document.createElement("div"), { id: "tag-suggest", className: "tag-suggest hidden" });
  tagAc.box.setAttribute("role", "listbox");
  document.body.appendChild(tagAc.box);
  tagAcLoadPref();
  $("tag-ac").onchange = () => {
    tagAc.enabled = $("tag-ac").checked;
    try { localStorage.setItem("tagAutocomplete", tagAc.enabled ? "on" : "off"); } catch { /* storage blocked: keep for this page only */ }
    if (!tagAc.enabled) tagAcHide();
  };
  for (const id of ["prompt", "negative"]) {
    const el = $(id);
    el.setAttribute("autocomplete", "off");
    el.addEventListener("input", () => tagAcQuery(el));
    el.addEventListener("keydown", (e) => {
      const open = tagAc.items.length && tagAc.input === el && !tagAc.box.classList.contains("hidden");
      if (!open) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const n = tagAc.items.length;
        tagAc.sel = (tagAc.sel + (e.key === "ArrowDown" ? 1 : n - 1)) % n; tagAcRender();
      } else if ((e.key === "Tab" && !e.shiftKey) || (e.key === "Enter" && !e.ctrlKey && !e.shiftKey)) {
        e.preventDefault();
        // Korean IME: let the composition commit first, then replace the token
        if (e.isComposing || e.keyCode === 229) setTimeout(tagAcAccept, 0); else tagAcAccept();
      } else if (e.key === "Escape") {
        e.preventDefault(); e.stopPropagation(); tagAcHide();
      }
    });
    el.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== el) tagAcHide(); }, 100));
    el.addEventListener("click", () => tagAcQuery(el));
    el.addEventListener("scroll", () => tagAc.items.length && tagAc.input === el && tagAcRender());
  }
  window.addEventListener("resize", () => tagAc.items.length && tagAcRender());
}

// ------------------------------------------------------------------ init
async function init() {
  INFO = await api("/api/info");
  fillSelect($("export"), Object.entries(INFO.export_sizes).map(([k, v]) => [k, v[0]]), "none");
  $("output-dir").value = INFO.settings.output_dir;
  initMaskEditor();
  initTagAutocomplete();
  renderAll();
  for (const id of ["variant", "steps", "cfg", "sampler", "scheduler", "width", "height", "negative", "cache-device"]) {
    $(id).addEventListener("change", () => {
      const s = stateFor(family);
      if (id === "variant") {
        const v = $("variant").value; s.variant = v;
        // switching variant loads that variant's preset defaults (steps/cfg differ a lot)
        if (fam().dynamic_variants) { Object.assign(s, presetValues(family, s.preset || "default", v), { loras: s.loras }); renderAll(); return; }
        const pid = Object.entries(fam().presets).find(([, p]) => p.variant === v)?.[0];
        if (pid) Object.assign(s, presetValues(family, pid), { width: s.width, height: s.height, size: s.size });
        renderAll(); return;
      }
      if (id === "width" || id === "height") s.size = "custom";
      saveForm(); renderForm();
    });
  }
  $("strength").oninput = () => { $("strength-val").textContent = (+$("strength").value).toFixed(2); saveForm(); };
  $("ref-strength").oninput = () => { $("ref-strength-val").textContent = (+$("ref-strength").value).toFixed(2); };
  $("generate").onclick = generate;
  $("cancel-current").onclick = async () => {
    const st = await api("/api/status");
    if (st.current) toast((await post(`/api/cancel/${st.current.id}`)).message);
  };
  $("path-load").onclick = async () => {
    try {
      const r = await post("/api/upload_path", { path: $("path-input").value });
      setSlot(slots.length, r); toast("경로의 이미지를 불러왔습니다.");
    } catch (e) { toast(e.message); }
  };
  $("btn-open-folder").onclick = () => post("/api/open_output", { record: lastResult && lastResult.id });
  $("btn-open-file").onclick = () => lastResult && post(`/api/open_file/${lastResult.id}`).catch((e) => toast(e.message));
  $("btn-reuse").onclick = () => lastResult && reuse(lastResult);
  $("btn-as-input").onclick = async () => {
    if (!lastResult) return;
    if (mode === "t2i") { const m = Object.entries(fam().modes).find(([k, v]) => k !== "t2i" && v.supported); if (m) mode = m[0]; renderAll(); }
    const r = await post(`/api/result_as_input/${lastResult.id}`);
    setSlot(slots.length, r); toast("결과 이미지를 참조 이미지로 넣었습니다.");
  };
  $("refresh-history").onclick = () => loadHistory();
  initHistoryPager();
  $("history-delete-all").onclick = () => deleteHistory(null);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("confirm-modal").classList.contains("hidden")) $("confirm-no").click(); });
  $("save-settings").onclick = async () => {
    try { await post("/api/settings", { output_dir: $("output-dir").value }); toast("출력 폴더를 저장했습니다."); }
    catch (e) { toast(e.message); }
  };
  $("btn-unload").onclick = async () => { try { await post("/api/unload"); toast("모델 메모리를 해제했습니다."); } catch (e) { toast(e.message); } };
  $("btn-restart").onclick = async () => { await post("/api/restart_engine"); toast("생성 엔진을 재시작합니다."); };
  $("btn-quit").onclick = async () => {
    if (!confirm("앱과 생성 엔진을 종료할까요?")) return;
    await post("/api/shutdown"); document.body.innerHTML = "<h2 style='padding:40px'>앱이 종료되었습니다. 이 탭을 닫아도 됩니다.</h2>";
  };
  document.addEventListener("keydown", (e) => { if (e.ctrlKey && e.key === "Enter") generate(); });
  loadHistory();
  poll(); setInterval(poll, 1000);
}
init().catch((e) => { document.body.innerHTML = `<h2 style="padding:40px">앱 서버에 연결할 수 없습니다: ${e.message}</h2>`; });
