// Utility function for DOM selection
const $ = s => document.querySelector(s);

// DOM elements
const input = $("#input");
const reportEl = $("#report");
const summaryEl = $("#summary");
const countEl = $("#count");

// Update reference count display
function updateCount() {
  const lines = input.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  countEl.textContent = `${lines.length} refs`;
  countEl.className = 'stat muted';
}

// Show loading bar with progress animation
function showLoadingBar() {
  const loadingContainer = document.getElementById('loading-container');
  const loadingProgress = document.querySelector('.loading-progress');

  loadingContainer.style.display = 'block';
  loadingProgress.style.width = '0%';

  // Animate progress bar
  let progress = 0;
  const interval = setInterval(() => {
    progress += 2.5;
    loadingProgress.style.width = progress + '%';
    if (progress >= 100) {
      clearInterval(interval);
    }
  }, 100);
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
  downloadBtn.disabled = true;
  reportPreview.textContent = 'Generating report...';
  reportEl.textContent = '';
  summaryEl.textContent = 'Processing...';

  try {
    const formData = new FormData();
    formData.append('references', input.value);

    const response = await fetch('/api/process', {
      method: 'POST',
      body: formData
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const result = await response.json();

    if (result.success) {
      reportEl.textContent = result.formatted_output || 'No references to display.';
      summaryEl.textContent = 'Analysis complete';
      summaryEl.className = 'stat ' + (result.summary.errors > 0 ? 'bad' : 'good');
      showPreviewSection(result.preview);
      downloadBtn.disabled = false;
    } else {
      throw new Error(result.error || 'Processing failed');
    }

  } catch (error) {
    console.error('Error processing references:', error);
    reportEl.textContent = `Error: ${error.message}. Please try again.`;
    summaryEl.textContent = 'Error occurred';
    summaryEl.className = 'stat bad';
    document.getElementById('report-preview').textContent = 'An error occurred while generating the report.';
  } finally {
    hideLoadingBar();
  }
}

// Event listeners
input.addEventListener('input', updateCount);

document.getElementById('check').addEventListener('click', runChecks);

document.getElementById('clear').addEventListener('click', () => {
  input.value = '';
  reportEl.textContent = '';
  summaryEl.textContent = 'Cleared';
  updateCount();
  document.getElementById('report-preview').textContent = 'No report generated. Enter references and click "Check".';
  document.getElementById('download-report').disabled = true;
  input.focus();
});

document.getElementById('copy').addEventListener('click', async () => {
  if (!reportEl.textContent) return;
  try {
    await navigator.clipboard.writeText(reportEl.textContent);
    summaryEl.textContent = 'Report copied';
    summaryEl.className = 'stat good';
    setTimeout(() => {
      summaryEl.textContent = 'Analysis complete';
    }, 2000);
  } catch (err) {
    alert('Copy failed. Please copy manually.');
  }
});

document.getElementById('download-report').addEventListener('click', async () => {
  if (!input.value.trim()) {
    alert('No references to download.');
    return;
  }

  const downloadBtn = document.getElementById('download-report');
  const originalText = downloadBtn.textContent;
  downloadBtn.disabled = true;
  downloadBtn.textContent = 'Generating...';

  try {
    const formData = new FormData();
    formData.append('references', input.value);

    const response = await fetch('/api/download-report', {
      method: 'POST',
      body: formData
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
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
    alert('Failed to download report. Please try again.');
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
  const invalidFiles = Array.from(files).filter(file => !validateFile(file));
  const validFiles = Array.from(files).filter(file => validateFile(file));

  if (invalidFiles.length > 0) {
    const extensions = allowedExtensions.join(', ');
    showWarning(`Invalid file format(s): ${invalidFiles.map(f => f.name).join(', ')}. Only ${extensions} files are supported.`);
  }

  validFiles.forEach(addFileToList);
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
    const msg = await resp.text();
    throw new Error(`Server extract failed (${resp.status}): ${msg}`);
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
    alert('Failed to extract text from files. ' + (error?.message || ''));
  } finally {
    processBtn.disabled = false;
    processBtn.textContent = originalText;
  }
}

// Initialize all functionality
document.addEventListener('DOMContentLoaded', () => {
  initializeTabs();
  initializeFileUpload();
  updateCount();
  input.focus();
});
