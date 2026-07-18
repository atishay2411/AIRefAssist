// Utility function for DOM selection
const $ = s => document.querySelector(s);

// DOM elements
const input = $("#input");
const reportEl = $("#report");
const summaryEl = $("#summary");
const countEl = $("#count");

// Most recent completed processing job — its stored results back the
// report download so the pipeline never runs twice for the same input.
let lastJobId = null;
// Job currently being polled (for the Cancel button).
let activeJobId = null;
// Per-reference results of the last completed run, and which references the
// user chose to keep as originally written (reject the corrections).
let currentResults = [];
const useOriginal = new Set();

// Update reference count display
function updateCount() {
  const lines = input.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  countEl.textContent = `${lines.length} refs`;
  countEl.className = 'stat muted';
}

// Real progress bar driven by the job's done/total counts
function showLoadingBar() {
  const loadingContainer = document.getElementById('loading-container');
  const loadingProgress = document.querySelector('.loading-progress');
  loadingContainer.style.display = 'block';
  loadingProgress.style.width = '3%';
  document.querySelector('.loading-text').textContent = 'Submitting…';
}

function updateProgress(done, total) {
  const pct = total > 0 ? Math.max(3, Math.round((done / total) * 100)) : 3;
  document.querySelector('.loading-progress').style.width = pct + '%';
  document.querySelector('.loading-text').textContent =
    `Processing reference ${Math.min(done + 1, total)} of ${total}…`;
}

// Hide loading bar
function hideLoadingBar() {
  document.getElementById('loading-container').style.display = 'none';
}

// Show preview section
function showPreviewSection(previewText) {
  const reportPreview = document.getElementById('report-preview');
  let modifiedPreview = previewText || 'No preview available.';
  modifiedPreview = modifiedPreview.replace(/Total references processed: \d+\s*\nSuccessfully processed: \d+\s*\nErrors encountered: \d+\s*\n*/, '');
  reportPreview.textContent = modifiedPreview;
}

// Run validation checks with backend API
async function runChecks() {
  const lines = input.value.split(/\r?\n/).filter(line => line.trim());
  const reportPreview = document.getElementById('report-preview');
  const downloadBtn = document.getElementById('download-report');

  if (lines.length === 0) {
    reportEl.textContent = 'No references to process.';
    summaryEl.textContent = 'No input';
    reportPreview.textContent = 'No report generated. Enter references and click "Check".';
    downloadBtn.disabled = true;
    return;
  }

  showLoadingBar();
  clearResults();
  downloadBtn.disabled = true;
  reportPreview.textContent = 'Generating report...';
  summaryEl.textContent = 'Processing...';

  try {
    const formData = new FormData();
    formData.append('references', input.value);

    // Submit as a background job and poll — a big batch can run for minutes
    // and must not depend on a single long-lived HTTP request.
    const createResp = await fetch('/api/jobs', { method: 'POST', body: formData });
    if (!createResp.ok) {
      let msg = `HTTP ${createResp.status}`;
      try { const j = await createResp.json(); if (j.detail) msg = j.detail; } catch (_) {}
      throw new Error(msg);
    }
    const { job_id, total } = await createResp.json();
    lastJobId = null;
    activeJobId = job_id;

    const result = await pollJob(job_id, total);
    renderCompleted(job_id, result);

  } catch (error) {
    console.error('Error processing references:', error);
    reportEl.textContent = `Error: ${error.message}. Please try again.`;
    summaryEl.textContent = 'Error occurred';
    summaryEl.className = 'stat bad';
    document.getElementById('report-preview').textContent = 'An error occurred while generating the report.';
  } finally {
    activeJobId = null;
    hideLoadingBar();
  }
}

// Poll a job until it finishes; keeps the progress bar honest.
async function pollJob(jobId, total) {
  for (;;) {
    const statusResp = await fetch(`/api/jobs/${jobId}`);
    if (!statusResp.ok) throw new Error(`Job status failed (HTTP ${statusResp.status})`);
    const s = await statusResp.json();

    if (s.status === 'completed') return s;
    if (s.status === 'failed') throw new Error(s.error || 'Processing failed');
    if (s.status === 'cancelled') throw new Error('Processing was cancelled');

    updateProgress(s.done, s.total || total);
    summaryEl.textContent = `Processing ${s.done}/${s.total || total}…`;
    await new Promise(r => setTimeout(r, 1500));
  }
}

function renderCompleted(jobId, result) {
  if (!result.success) throw new Error(result.error || 'Processing failed');

  lastJobId = jobId;  // report download reuses stored results
  currentResults = result.results || [];
  useOriginal.clear();

  renderResultCards(currentResults);
  reportEl.style.display = 'none';

  const notes = [];
  if (result.summary.retracted > 0) notes.push(`⚠ ${result.summary.retracted} RETRACTED`);
  if (result.summary.duplicates > 0) notes.push(`${result.summary.duplicates} duplicate(s)`);
  if (result.summary.errors > 0) notes.push(`${result.summary.errors} error(s)`);
  summaryEl.textContent = 'Analysis complete' + (notes.length ? ' — ' + notes.join(', ') : '');
  summaryEl.className = 'stat ' + (notes.length ? 'bad' : 'good');

  showPreviewSection(result.preview);
  document.getElementById('download-report').disabled = false;
  document.getElementById('copy-bibtex').disabled =
    !currentResults.some(e => e.bibtex);

  // Results survive a refresh: the job is retrievable for an hour.
  const url = new URL(window.location);
  url.searchParams.set('job', jobId);
  history.replaceState(null, '', url);
}

// ---- Structured per-reference result cards ----

function badge(text, cls) {
  const b = document.createElement('span');
  b.className = 'badge ' + cls;
  b.textContent = text;
  return b;
}

function statusBadge(entry) {
  if (entry.status === 'error') return badge('error', 'badge-error');
  switch (entry.resolution) {
    case 'corrected':  return badge('corrected', 'badge-corrected');
    case 'verified':   return badge('verified', 'badge-verified');
    case 'unverified': return badge('unverified', 'badge-unverified');
    case 'suspect':    return badge('⚠ likely fabricated', 'badge-error');
    case 'rejected':   return badge('not a reference', 'badge-error');
    default:           return badge(entry.resolution || 'processed', 'badge-unverified');
  }
}

function renderResultCards(results) {
  const container = document.getElementById('results');
  container.innerHTML = '';

  for (const entry of results) {
    const card = document.createElement('div');
    card.className = 'result-card' + (entry.retracted ? ' retracted' : '');

    // Header: number + badges + per-card actions
    const head = document.createElement('div');
    head.className = 'result-head';
    const num = document.createElement('span');
    num.className = 'result-num';
    num.textContent = `[${entry.idx}]`;
    head.appendChild(num);
    head.appendChild(statusBadge(entry));
    if (entry.confidence && entry.confidence !== 'n/a') {
      head.appendChild(badge(`confidence: ${entry.confidence}`, 'badge-muted'));
    }
    if (entry.retracted) head.appendChild(badge('⚠ RETRACTED', 'badge-retracted'));
    if (entry.duplicate_of) head.appendChild(badge(`duplicate of [${entry.duplicate_of}]`, 'badge-warn'));

    const actions = document.createElement('span');
    actions.className = 'result-actions';
    const copyBtn = document.createElement('button');
    copyBtn.className = 'btn-ghost btn-small';
    copyBtn.textContent = 'Copy';
    copyBtn.title = 'Copy this reference';
    copyBtn.addEventListener('click', async () => {
      await navigator.clipboard.writeText(selectedText(entry));
      copyBtn.textContent = 'Copied ✓';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
    });
    actions.appendChild(copyBtn);
    head.appendChild(actions);
    card.appendChild(head);

    // The reference text (corrected version by default)
    const refText = document.createElement('div');
    refText.className = 'result-ref';
    refText.textContent = selectedText(entry);
    card.appendChild(refText);

    if (entry.retracted) {
      const note = document.createElement('div');
      note.className = 'result-retraction';
      note.textContent = 'This work has been retracted by its publisher — do not cite it without noting the retraction.';
      card.appendChild(note);
    }

    // Corrections: reviewable, with an accept/keep-original choice
    if (entry.corrections && entry.corrections.length) {
      const det = document.createElement('details');
      det.className = 'result-corrections';
      const sum = document.createElement('summary');
      sum.textContent = `${entry.corrections.length} correction(s) applied`;
      det.appendChild(sum);
      for (const c of entry.corrections) {
        const row = document.createElement('div');
        row.className = 'correction-row';
        row.innerHTML = '';
        const fld = document.createElement('span'); fld.className = 'corr-field'; fld.textContent = c.field;
        const oldV = document.createElement('span'); oldV.className = 'corr-old'; oldV.textContent = c.old;
        const arrow = document.createElement('span'); arrow.className = 'corr-arrow'; arrow.textContent = '→';
        const newV = document.createElement('span'); newV.className = 'corr-new'; newV.textContent = c.new;
        const src = document.createElement('span'); src.className = 'corr-src'; src.textContent = c.source;
        row.append(fld, oldV, arrow, newV, src);
        det.appendChild(row);
      }
      const toggleWrap = document.createElement('label');
      toggleWrap.className = 'use-original';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = useOriginal.has(entry.idx);
      cb.addEventListener('change', () => {
        if (cb.checked) useOriginal.add(entry.idx); else useOriginal.delete(entry.idx);
        refText.textContent = selectedText(entry);
      });
      toggleWrap.appendChild(cb);
      toggleWrap.appendChild(document.createTextNode(' Keep my original text (reject corrections)'));
      det.appendChild(toggleWrap);
      card.appendChild(det);
    }

    // Warnings
    for (const w of entry.warnings || []) {
      const warn = document.createElement('div');
      warn.className = 'result-warning';
      warn.textContent = '⚠ ' + w;
      card.appendChild(warn);
    }

    container.appendChild(card);
  }
}

function selectedText(entry) {
  return useOriginal.has(entry.idx) ? entry.original : entry.formatted;
}

// Final reference list honoring per-reference original/corrected choices.
function buildOutputText() {
  return currentResults.map(e => `[${e.idx}] ${selectedText(e)}`).join('\n');
}

// Event listeners
input.addEventListener('input', () => {
  updateCount();
  // Edited input invalidates the stored job results — downloads and BibTeX
  // must come from a fresh Check.
  lastJobId = null;
  document.getElementById('download-report').disabled = true;
  localStorage.setItem('refassist-draft', input.value);
});

document.getElementById('check').addEventListener('click', runChecks);

const SAMPLE_REFS = `[1] Y. LeCun, Y. Bengio, and G. Hinton, "Deep learnng," Nature, vol. 521, no. 7553, pp. 436-444, 2015.
[2] A. Vaswani et al., "Attention is all you need," in Advances in Neural Information Processing Systems, 2015, pp. 5998-6008.
[3] A. J. Wakefield et al., "Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children," The Lancet, vol. 351, no. 9103, pp. 637-641, 1998.`;

document.getElementById('example').addEventListener('click', () => {
  input.value = SAMPLE_REFS;
  updateCount();
  lastJobId = null;
  summaryEl.textContent = 'Sample loaded — note the typo in [1], the wrong year in [2], and [3] is a retracted paper. Click Check.';
  summaryEl.className = 'stat muted';
  input.focus();
});

function clearResults() {
  reportEl.textContent = '';
  reportEl.style.display = '';
  document.getElementById('results').innerHTML = '';
  currentResults = [];
  useOriginal.clear();
  document.getElementById('copy-bibtex').disabled = true;
  const url = new URL(window.location);
  url.searchParams.delete('job');
  history.replaceState(null, '', url);
}

document.getElementById('clear').addEventListener('click', () => {
  input.value = '';
  localStorage.removeItem('refassist-draft');
  clearResults();
  summaryEl.textContent = 'Cleared';
  updateCount();
  document.getElementById('report-preview').textContent = 'No report generated. Enter references and click "Check".';
  document.getElementById('download-report').disabled = true;
  input.focus();
});

document.getElementById('cancel-job').addEventListener('click', async () => {
  if (!activeJobId) return;
  try { await fetch(`/api/jobs/${activeJobId}`, { method: 'DELETE' }); } catch (_) {}
});

document.getElementById('copy').addEventListener('click', async () => {
  const text = currentResults.length ? buildOutputText() : reportEl.textContent;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    summaryEl.textContent = 'Reference list copied';
    summaryEl.className = 'stat good';
    setTimeout(() => { summaryEl.textContent = 'Analysis complete'; }, 2000);
  } catch (err) {
    summaryEl.textContent = 'Copy failed — select and copy manually';
    summaryEl.className = 'stat bad';
  }
});

document.getElementById('copy-bibtex').addEventListener('click', async () => {
  const bib = currentResults.filter(e => e.bibtex).map(e => e.bibtex).join('\n\n');
  if (!bib) return;
  try {
    await navigator.clipboard.writeText(bib);
    summaryEl.textContent = 'BibTeX copied';
    summaryEl.className = 'stat good';
    setTimeout(() => { summaryEl.textContent = 'Analysis complete'; }, 2000);
  } catch (err) {
    summaryEl.textContent = 'Copy failed — download the report instead';
    summaryEl.className = 'stat bad';
  }
});

document.getElementById('download-report').addEventListener('click', async () => {
  // The report always comes from the completed job's STORED results — never
  // from a second synchronous pipeline run (which could time out and could
  // disagree with the results already on screen).
  if (!lastJobId) {
    summaryEl.textContent = 'Run Check first — the report is built from those results';
    summaryEl.className = 'stat bad';
    return;
  }

  const downloadBtn = document.getElementById('download-report');
  const originalText = downloadBtn.textContent;
  downloadBtn.disabled = true;
  downloadBtn.textContent = 'Generating...';

  try {
    const response = await fetch(`/api/jobs/${lastJobId}/report`);
    if (!response.ok) {
      let msg = `HTTP ${response.status}`;
      try { const j = await response.json(); if (j.detail) msg = j.detail; } catch (_) {}
      throw new Error(msg);
    }

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'refassist_report.zip';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    summaryEl.textContent = 'Full report downloaded';
    summaryEl.className = 'stat good';

  } catch (error) {
    console.error('Error downloading report:', error);
    summaryEl.textContent = 'Download failed — please try again';
    summaryEl.className = 'stat bad';
  } finally {
    downloadBtn.disabled = false;
    downloadBtn.textContent = originalText;
    setTimeout(() => {
      if (summaryEl.textContent === 'Full report downloaded') {
        summaryEl.textContent = 'Analysis complete';
      }
    }, 2000);
  }
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    runChecks();
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    document.getElementById('clear').click();
  }
});

// Theme toggle functionality
const themeToggle = $("#theme-toggle");
const themeIcon = $(".theme-icon");

const savedTheme = localStorage.getItem('theme') || 'dark';
if (savedTheme === 'light') {
  document.documentElement.classList.add('light-mode');
  themeIcon.textContent = '☀️';
} else {
  themeIcon.textContent = '🌙';
}

themeToggle.addEventListener('click', () => {
  const isLight = document.documentElement.classList.toggle('light-mode');
  localStorage.setItem('theme', isLight ? 'light' : 'dark');
  themeIcon.textContent = isLight ? '☀️' : '🌙';
});

// File upload functionality
const uploadedFiles = [];
// Server supports: .docx, .pdf, .tex, .bbl, .txt (legacy .doc is rejected server-side)
const allowedExtensions = ['.docx', '.pdf', '.tex', '.bbl', '.txt'];
// Mirror the server limits (REFASSIST_MAX_UPLOAD_BYTES / REFASSIST_MAX_FILES)
// so obvious problems are caught before any bytes are uploaded.
const MAX_FILE_BYTES = 15 * 1024 * 1024;
const MAX_FILES = 10;

function initializeTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetTab = btn.dataset.tab;
      tabBtns.forEach(b => b.classList.remove('active'));
      tabContents.forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`${targetTab}-tab`).classList.add('active');
    });
  });
}

function validateFile(file) {
  const extension = '.' + file.name.split('.').pop().toLowerCase();
  return allowedExtensions.includes(extension);
}

function getFileIcon(filename) {
  const extension = '.' + filename.split('.').pop().toLowerCase();
  const icons = {
    '.pdf': '📄', '.docx': '📝', '.tex': '📋', '.bbl': '📋', '.txt': '📄'
  };
  return icons[extension] || '📄';
}

function formatFileSize(bytes) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function updateFileCount() {
  const fileCountEl = $("#file-count");
  const processBtn = $("#process-files");
  fileCountEl.textContent = `${uploadedFiles.length} files`;
  processBtn.disabled = uploadedFiles.length === 0;
}

function addFileToList(file) {
  const fileList = $("#file-list");
  const fileId = Date.now() + Math.random();

  const fileItem = document.createElement('div');
  fileItem.className = 'file-item';
  fileItem.dataset.fileId = fileId;

  fileItem.innerHTML = `
    <div class="file-icon">${getFileIcon(file.name)}</div>
    <div class="file-info">
      <p class="file-name" title="${file.name}">${file.name}</p>
      <p class="file-size">${formatFileSize(file.size)}</p>
    </div>
    <div class="file-status ready">Ready</div>
    <button class="file-remove" title="Remove file">×</button>
  `;

  fileItem.querySelector('.file-remove').addEventListener('click', () => {
    const index = uploadedFiles.findIndex(f => f.id === fileId);
    if (index > -1) {
      uploadedFiles.splice(index, 1);
      fileItem.remove();
      updateFileCount();
    }
  });

  fileList.appendChild(fileItem);
  uploadedFiles.push({ id: fileId, file, element: fileItem });
  updateFileCount();
}

function showWarning(message) {
  const uploadTab = $("#upload-tab");
  let warningDiv = uploadTab.querySelector('.warning-message');
  if (!warningDiv) {
    warningDiv = document.createElement('div');
    warningDiv.className = 'warning-message';
    uploadTab.insertBefore(warningDiv, uploadTab.firstChild);
  }
  warningDiv.innerHTML = `<span class="warning-icon">⚠️</span> ${message}`;

  setTimeout(() => {
    if (warningDiv.parentNode) {
      warningDiv.remove();
    }
  }, 5000);
}

function handleFiles(files) {
  const incoming = Array.from(files);

  const badType = incoming.filter(f => !validateFile(f));
  if (badType.length > 0) {
    showWarning(`Invalid file format(s): ${badType.map(f => f.name).join(', ')}. Only ${allowedExtensions.join(', ')} files are supported.`);
  }

  const tooBig = incoming.filter(f => validateFile(f) && f.size > MAX_FILE_BYTES);
  if (tooBig.length > 0) {
    showWarning(`File(s) over the ${Math.round(MAX_FILE_BYTES / 1048576)} MB limit: ${tooBig.map(f => f.name).join(', ')}.`);
  }

  const empty = incoming.filter(f => validateFile(f) && f.size === 0);
  if (empty.length > 0) {
    showWarning(`Empty file(s) skipped: ${empty.map(f => f.name).join(', ')}.`);
  }

  const validFiles = incoming.filter(f => validateFile(f) && f.size > 0 && f.size <= MAX_FILE_BYTES);

  for (const file of validFiles) {
    if (uploadedFiles.length >= MAX_FILES) {
      showWarning(`Maximum ${MAX_FILES} files per batch — the rest were skipped.`);
      break;
    }
    // Skip duplicates already in the list
    if (uploadedFiles.some(u => u.file.name === file.name && u.file.size === file.size)) {
      continue;
    }
    addFileToList(file);
  }
}

function initializeFileUpload() {
  const uploadArea = $("#upload-area");
  const fileInput = $("#file-input");

  uploadArea.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => {
    handleFiles(e.target.files);
    e.target.value = '';
  });

  ['dragover', 'dragleave', 'drop'].forEach(eventName => {
    uploadArea.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (eventName === 'dragover') uploadArea.classList.add('dragover');
      if (eventName === 'dragleave' || eventName === 'drop') uploadArea.classList.remove('dragover');
      if (eventName === 'drop') handleFiles(e.dataTransfer.files);
    });
  });

  $("#process-files").addEventListener('click', processUploadedFiles);

  $("#clear-files").addEventListener('click', () => {
    uploadedFiles.length = 0;
    $("#file-list").innerHTML = '';
    updateFileCount();
    const warning = $("#upload-tab .warning-message");
    if (warning) warning.remove();
  });
}

// ---- NEW: server-side extraction ----
async function extractTextFromServer(files) {
  const formData = new FormData();
  for (const f of files) formData.append('files', f);

  const resp = await fetch('/api/extract', { method: 'POST', body: formData });
  if (!resp.ok) {
    // Surface the server's human-readable message, not a raw JSON dump.
    let msg = `Server error (HTTP ${resp.status})`;
    try {
      const err = await resp.json();
      if (err && err.detail) msg = err.detail;
    } catch (_) { /* non-JSON error body */ }
    throw new Error(msg);
  }
  const data = await resp.json();
  return data.text || '';
}

async function processUploadedFiles() {
  const processBtn = $("#process-files");
  const originalText = processBtn.textContent;
  processBtn.disabled = true;
  processBtn.textContent = 'Processing...';

  // Set all to "Processing"
  for (const { element } of uploadedFiles) {
    const statusEl = element.querySelector('.file-status');
    statusEl.textContent = 'Processing';
    statusEl.className = 'file-status processing';
  }

  try {
    const files = uploadedFiles.map(f => f.file);
    const extracted = await extractTextFromServer(files);

    if (extracted.trim()) {
      input.value = extracted.trim();
      updateCount();
      document.querySelector('.tab-btn[data-tab="paste"]').click();
      await runChecks();
    }

    // Mark success
    for (const { element } of uploadedFiles) {
      const statusEl = element.querySelector('.file-status');
      statusEl.textContent = 'Processed';
      statusEl.className = 'file-status ready';
    }
  } catch (error) {
    console.error('Error extracting files on server:', error);
    for (const { element } of uploadedFiles) {
      const statusEl = element.querySelector('.file-status');
      statusEl.textContent = 'Error';
      statusEl.className = 'file-status error';
    }
    showWarning('Failed to extract text from files: ' + (error?.message || 'unknown error'));
  } finally {
    processBtn.disabled = false;
    processBtn.textContent = originalText;
  }
}

// Restore a completed job from the URL (?job=…) — results survive refresh
// and links are shareable while the job is retained server-side.
async function restoreFromUrl() {
  const jobId = new URLSearchParams(window.location.search).get('job');
  if (!jobId) return;
  try {
    const resp = await fetch(`/api/jobs/${jobId}`);
    if (!resp.ok) return;
    const s = await resp.json();
    if (s.status === 'completed') {
      renderCompleted(jobId, s);
      summaryEl.textContent += ' (restored)';
    } else if (s.status === 'running' || s.status === 'queued') {
      activeJobId = jobId;
      showLoadingBar();
      try {
        renderCompleted(jobId, await pollJob(jobId, s.total));
      } finally {
        activeJobId = null;
        hideLoadingBar();
      }
    }
  } catch (_) { /* expired or unreachable job — start fresh */ }
}

// Initialize all functionality
document.addEventListener('DOMContentLoaded', () => {
  initializeTabs();
  initializeFileUpload();
  const draft = localStorage.getItem('refassist-draft');
  if (draft && !input.value) input.value = draft;
  updateCount();
  restoreFromUrl();
  input.focus();
});
