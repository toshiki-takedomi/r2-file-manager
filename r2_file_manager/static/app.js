(() => {
  "use strict";

  const token = document.querySelector('meta[name="r2fm-token"]').content;
  const $ = (selector) => document.querySelector(selector);
  const {
    addSelection,
    buildDownloadOutputs,
    selectVisible,
  } = globalThis.R2DownloadUtils;
  const state = {
    configured: false,
    connectionName: "未接続",
    bucket: null,
    prefix: "",
    nextToken: null,
    selected: new Set(),
    batchDownloadSelected: new Map(),
    batchDownloadVisible: new Map(),
    batchDownloadPrefix: "",
    batchDownloadNextToken: null,
    batchDownloadSearchQuery: "",
    batchDownloadRequestId: 0,
    batchDownloadOutputValues: null,
    batchDownloadTab: "url",
    transfers: new Map(),
    moveJobs: new Map(),
    pendingFiles: [],
    actionObject: null,
    metricsRefreshTimers: [],
  };

  class ApiError extends Error {
    constructor(message, status, code) {
      super(message);
      this.status = status;
      this.code = code;
    }
  }

  async function api(path, options = {}) {
    const init = { method: options.method || "GET", headers: { ...(options.headers || {}) } };
    init.headers["X-R2FM-Token"] = token;
    if (options.data !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.data);
    } else if (options.body !== undefined) {
      init.body = options.body;
    }
    const response = await fetch(path, init);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(payload.error || "処理に失敗しました。", response.status, payload.code);
    return payload;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[char]);
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return "--";
    if (bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / (1024 ** index);
    return `${value.toFixed(index === 0 ? 0 : value >= 10 ? 1 : 2)} ${units[index]}`;
  }

  function formatDate(value) {
    if (!value) return "--";
    return new Intl.DateTimeFormat("ja-JP", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).format(new Date(value));
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "計算中";
    if (seconds < 60) return `残り約${Math.ceil(seconds)}秒`;
    if (seconds < 3600) return `残り約${Math.ceil(seconds / 60)}分`;
    return `残り約${(seconds / 3600).toFixed(1)}時間`;
  }

  function toast(message, kind = "normal") {
    const element = document.createElement("div");
    element.className = `toast ${kind === "error" ? "error" : ""}`;
    element.textContent = message;
    $("#toast-region").append(element);
    setTimeout(() => element.remove(), 4500);
  }

  function setBusy(button, busy, label = "処理中…") {
    if (busy) {
      button.dataset.busyHtml = button.innerHTML;
      button.textContent = label;
      button.disabled = true;
    } else {
      if (button.dataset.busyHtml) {
        button.innerHTML = button.dataset.busyHtml;
        delete button.dataset.busyHtml;
      }
      button.disabled = false;
    }
  }

  function showInline(selector, message, success = false) {
    const element = $(selector);
    element.textContent = message;
    element.classList.toggle("success", success);
    element.classList.remove("hidden");
  }

  function hideInline(selector) { $(selector).classList.add("hidden"); }

  function confirmAction(title, text, actionLabel = "削除") {
    const dialog = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-text").textContent = text;
    $("#confirm-action").textContent = actionLabel;
    dialog.showModal();
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
    });
  }

  async function loadInitialSettings() {
    const payload = await api("/api/settings");
    state.configured = payload.configured;
    state.connectionName = payload.settings.name || "未接続";
    $("#connection-name").textContent = state.connectionName;
    fillSettings(
      payload.settings,
      payload.secret_access_key || "",
      payload.secret_configured,
      payload.cloudflare_api_token || "",
      payload.metrics_token_configured,
    );
    if (!payload.configured) {
      $("#settings-dialog").dataset.required = "true";
      $("#settings-dialog").showModal();
      return;
    }
    await Promise.all([loadBuckets(), loadMetrics(), loadIncompleteUploads(), loadMoveJobs()]);
  }

  function fillSettings(settings, secret = "", configured = false, metricsToken = "", metricsConfigured = false) {
    const form = $("#settings-form");
    for (const name of ["name", "account_id", "access_key_id", "public_url"]) {
      form.elements[name].value = settings[name] || "";
    }
    form.elements.secret_access_key.value = secret;
    form.elements.cloudflare_api_token.value = metricsToken;
    const secretBadge = $("#secret-badge");
    const secretRow = form.elements.secret_access_key.closest(".password-row");
    secretBadge.textContent = configured ? "✓ 設定済み" : secret ? "未保存" : "未設定";
    secretBadge.classList.toggle("configured", configured);
    secretBadge.classList.toggle("loaded", !configured && Boolean(secret));
    secretRow.classList.toggle("configured", configured);
    form.elements.secret_access_key.placeholder = configured ? "保存済み（変更する場合のみ入力）" : "Secret Access Keyを入力";
    $("#secret-status").textContent = configured
      ? "資格情報マネージャーに設定済み。変更する場合のみ入力してください。"
      : secret ? "環境変数から読み込みました。" : "未設定";
    const metricsBadge = $("#metrics-token-badge");
    const metricsRow = form.elements.cloudflare_api_token.closest(".password-row");
    metricsBadge.textContent = metricsConfigured ? "✓ 設定済み" : metricsToken ? "未保存" : "未設定";
    metricsBadge.classList.toggle("configured", metricsConfigured);
    metricsBadge.classList.toggle("loaded", !metricsConfigured && Boolean(metricsToken));
    metricsRow.classList.toggle("configured", metricsConfigured);
    form.elements.cloudflare_api_token.placeholder = metricsConfigured ? "保存済み（変更する場合のみ入力）" : "任意のAPI Tokenを入力";
    $("#metrics-token-status").textContent = metricsConfigured
      ? "資格情報マネージャーに設定済み。変更する場合のみ入力してください。"
      : metricsToken ? "環境変数から読み込みました。" : "AccountのR2読み取り権限を持つAPI Tokenを指定してください。";
  }

  function settingsValues() {
    const form = $("#settings-form");
    return Object.fromEntries(new FormData(form).entries());
  }

  async function openSettings() {
    const payload = await api("/api/settings");
    fillSettings(payload.settings, "", payload.secret_configured, "", payload.metrics_token_configured);
    hideInline("#settings-message");
    $("#settings-dialog").dataset.required = "false";
    $("#settings-dialog").showModal();
  }

  async function testSettings() {
    const button = $("#test-settings");
    hideInline("#settings-message");
    setBusy(button, true, "接続中…");
    try {
      await api("/api/settings/test", { method: "POST", data: settingsValues() });
      showInline("#settings-message", "R2へ正常に接続できました。", true);
    } catch (error) {
      showInline("#settings-message", error.message);
    } finally { setBusy(button, false); }
  }

  async function saveSettings() {
    const button = $("#save-settings");
    hideInline("#settings-message");
    setBusy(button, true, "確認中…");
    try {
      const result = await api("/api/settings", { method: "POST", data: settingsValues() });
      state.configured = true;
      state.connectionName = result.name;
      $("#connection-name").textContent = result.name;
      $("#settings-dialog").dataset.required = "false";
      $("#settings-dialog").close();
      toast("接続設定を保存しました。");
      showBucketView();
      await Promise.all([loadBuckets(), loadMetrics()]);
    } catch (error) {
      showInline("#settings-message", error.message);
    } finally { setBusy(button, false); }
  }

  async function loadEnvironment() {
    try {
      const values = await api("/api/settings/environment");
      fillSettings(values, values.secret_access_key || "", false, values.cloudflare_api_token || "", false);
      showInline("#settings-message", "環境変数をフォームへ反映しました。保存はまだ行われていません。", true);
    } catch (error) { showInline("#settings-message", error.message); }
  }

  function showBucketView() {
    state.bucket = null;
    state.prefix = "";
    $("#bucket-view").classList.remove("hidden");
    $("#object-view").classList.add("hidden");
    loadMetrics({ quiet: true });
  }

  async function loadBuckets() {
    const rows = $("#bucket-rows");
    rows.innerHTML = '<tr><td colspan="3">読み込み中…</td></tr>';
    $("#bucket-empty").classList.add("hidden");
    try {
      const { buckets } = await api("/api/buckets");
      rows.innerHTML = "";
      $("#bucket-empty").classList.toggle("hidden", buckets.length !== 0);
      for (const bucket of buckets) {
        const row = document.createElement("tr");
        row.innerHTML = `
          <td><button class="row-link bucket-link"><span class="row-name"><span class="folder-icon">▱</span>${escapeHtml(bucket.name)}</span></button></td>
          <td>${formatDate(bucket.created_at)}</td>
          <td><button class="row-menu delete-bucket" title="削除">⋯</button></td>`;
        row.querySelector(".bucket-link").addEventListener("click", () => openBucket(bucket.name));
        row.querySelector(".delete-bucket").addEventListener("click", () => deleteBucket(bucket.name));
        rows.append(row);
      }
    } catch (error) {
      rows.innerHTML = `<tr><td colspan="3" class="status-error">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  async function loadMetrics({ quiet = false } = {}) {
    const metricCards = document.querySelectorAll("#usage-panel .usage-card:not(.usage-unavailable)");
    const unavailable = $("#usage-unavailable");
    metricCards.forEach((card) => card.classList.remove("hidden"));
    unavailable.classList.add("hidden");
    if (!quiet) {
      $("#usage-storage").textContent = "--";
      $("#usage-objects").textContent = "--";
      $("#usage-uploading").textContent = "--";
      $("#usage-storage-detail").textContent = "Cloudflareから取得中";
      $("#usage-updated-at").textContent = "";
    }
    try {
      const result = await api("/api/metrics");
      if (!result.configured) {
        metricCards.forEach((card) => card.classList.add("hidden"));
        unavailable.classList.remove("hidden");
        unavailable.querySelector("strong").textContent = "API Tokenが必要です";
        return;
      }
      const metrics = result.metrics;
      $("#usage-storage").textContent = formatBytes(metrics.stored_bytes);
      $("#usage-objects").textContent = new Intl.NumberFormat("ja-JP").format(metrics.objects);
      $("#usage-uploading").textContent = formatBytes(metrics.uploading_bytes);
      $("#usage-storage-detail").textContent = `Standard ${formatBytes(metrics.storage_classes.standard.stored_bytes)}・IA ${formatBytes(metrics.storage_classes.infrequent_access.stored_bytes)}`;
      $("#usage-updated-at").textContent = `${new Intl.DateTimeFormat("ja-JP", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date())} 更新・Cloudflare集計値の反映には時間がかかる場合があります`;
    } catch (error) {
      metricCards.forEach((card) => card.classList.add("hidden"));
      unavailable.classList.remove("hidden");
      unavailable.querySelector("strong").textContent = `取得できませんでした: ${error.message}`;
    }
  }

  function scheduleMetricsRefresh() {
    for (const timer of state.metricsRefreshTimers) clearTimeout(timer);
    state.metricsRefreshTimers = [0, 10_000, 30_000, 60_000, 120_000, 300_000].map((delay) => (
      setTimeout(() => loadMetrics({ quiet: true }), delay)
    ));
  }

  async function createBucket(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    hideInline("#bucket-message");
    setBusy(button, true);
    try {
      await api("/api/buckets", { method: "POST", data: { name: form.elements.name.value.trim() } });
      $("#bucket-dialog").close();
      toast("バケットを作成しました。");
      await loadBuckets();
    } catch (error) { showInline("#bucket-message", error.message); }
    finally { setBusy(button, false); }
  }

  async function deleteBucket(name) {
    const confirmed = await confirmAction(
      "バケットを削除",
      `「${name}」を削除しますか？\n空のバケットだけ削除できます。この操作は取り消せません。`,
    );
    if (!confirmed) return;
    try {
      await api(`/api/buckets/${encodeURIComponent(name)}`, { method: "DELETE" });
      toast("バケットを削除しました。");
      await loadBuckets();
    } catch (error) { toast(error.message, "error"); }
  }

  async function openBucket(name, prefix = "") {
    state.bucket = name;
    state.prefix = prefix;
    state.nextToken = null;
    state.selected.clear();
    $("#bucket-view").classList.add("hidden");
    $("#object-view").classList.remove("hidden");
    renderBreadcrumbs();
    updateSelectionControls();
    await loadObjects(false);
  }

  function renderBreadcrumbs() {
    const nav = $("#breadcrumbs");
    nav.innerHTML = "";
    const rootButton = document.createElement("button");
    rootButton.textContent = "バケット";
    rootButton.addEventListener("click", showBucketView);
    nav.append(rootButton, separator());

    const bucketButton = document.createElement("button");
    bucketButton.textContent = state.bucket;
    bucketButton.addEventListener("click", () => openBucket(state.bucket, ""));
    nav.append(bucketButton);
    const parts = state.prefix.split("/").filter(Boolean);
    let accumulated = "";
    parts.forEach((part) => {
      accumulated += `${part}/`;
      nav.append(separator());
      const button = document.createElement("button");
      button.textContent = part;
      const target = accumulated;
      button.addEventListener("click", () => openBucket(state.bucket, target));
      nav.append(button);
    });
  }

  function separator() {
    const span = document.createElement("span");
    span.className = "breadcrumb-separator";
    span.textContent = "/";
    return span;
  }

  async function loadObjects(append = false) {
    const rows = $("#object-rows");
    if (!append) rows.innerHTML = '<tr><td colspan="7">読み込み中…</td></tr>';
    const params = new URLSearchParams({ bucket: state.bucket, prefix: state.prefix });
    if (append && state.nextToken) params.set("continuation_token", state.nextToken);
    try {
      const result = await api(`/api/objects?${params}`);
      if (!append) rows.innerHTML = "";
      for (const folder of result.folders) rows.append(folderRow(folder));
      for (const object of result.objects) rows.append(objectRow(object));
      state.nextToken = result.next_token;
      $("#load-more").classList.toggle("hidden", !state.nextToken);
      $("#object-empty").classList.toggle("hidden", rows.children.length !== 0);
      $("#select-all").checked = false;
    } catch (error) {
      rows.innerHTML = `<tr><td colspan="7" class="status-error">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  function folderRow(folder) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td></td><td><button class="row-link"><span class="row-name"><span class="folder-icon">□</span>${escapeHtml(folder.name)}/</span></button></td>
      <td>フォルダー</td><td>--</td><td>--</td><td>--</td><td></td>`;
    row.querySelector("button").addEventListener("click", () => openBucket(state.bucket, folder.prefix));
    return row;
  }

  function objectRow(object) {
    const row = document.createElement("tr");
    row.dataset.key = object.key;
    row.innerHTML = `
      <td class="check-column"><input type="checkbox" aria-label="${escapeHtml(object.name)}を選択"></td>
      <td><span class="row-name"><span class="file-icon">▤</span>${escapeHtml(object.name)}</span></td>
      <td>ファイル</td><td>${escapeHtml(object.storage_class || "STANDARD")}</td><td>${formatBytes(object.size)}</td><td>${formatDate(object.last_modified)}</td>
      <td><button class="row-menu" title="ファイル操作" aria-label="${escapeHtml(object.name)}の操作">⋯</button></td>`;
    const checkbox = row.querySelector('input[type="checkbox"]');
    checkbox.checked = state.selected.has(object.key);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selected.add(object.key); else state.selected.delete(object.key);
      updateSelectionControls();
    });
    row.querySelector(".row-menu").addEventListener("click", () => openObjectActions(object));
    return row;
  }

  function openObjectActions(object) {
    state.actionObject = object;
    $("#object-action-name").textContent = object.key;
    $("#object-actions-dialog").showModal();
  }

  async function copyText(value) {
    try {
      await navigator.clipboard.writeText(value);
      toast("クリップボードにコピーしました。");
    } catch (_error) {
      const area = document.createElement("textarea");
      area.value = value;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
      toast("クリップボードにコピーしました。");
    }
  }

  async function showDownloadInfo() {
    const object = state.actionObject;
    if (!object || !state.bucket) return;
    $("#object-actions-dialog").close();
    const button = $("#show-download-info");
    try {
      setBusy(button, true, "URLを生成中…");
      const params = new URLSearchParams({ bucket: state.bucket, key: object.key });
      const info = await api(`/api/objects/download-info?${params}`);
      const outputs = buildDownloadOutputs([{ key: object.key, url: info.url }]);
      $("#download-file-name").textContent = object.key;
      $("#download-url").value = info.url;
      $("#download-url-kind").textContent = info.public ? "公開URL" : `署名URL（${Math.round(info.expires_in / 60)}分有効）`;
      $("#download-url-kind").classList.toggle("configured", info.public);
      $("#download-url-kind").classList.toggle("loaded", !info.public);
      $("#download-note").textContent = info.public
        ? "Public URL設定から生成した期限のないURLです。"
        : "URLには認証情報が含まれます。有効期限内は第三者に共有しないでください。";
      $("#curl-command").textContent = outputs.curl;
      $("#wget-command").textContent = outputs.wget;
      $("#download-dialog").showModal();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  async function downloadObject() {
    const object = state.actionObject;
    if (!object || !state.bucket) return;
    const button = $("#download-object");
    try {
      setBusy(button, true, "ダウンロードを準備中…");
      const params = new URLSearchParams({ bucket: state.bucket, key: object.key });
      const info = await api(`/api/objects/download-url?${params}`);
      const link = document.createElement("a");
      link.href = info.url;
      link.download = info.file_name;
      document.body.append(link);
      link.click();
      link.remove();
      $("#object-actions-dialog").close();
      toast("ダウンロードを開始しました。");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  function resetBatchDownloadState() {
    state.batchDownloadRequestId += 1;
    state.batchDownloadSelected.clear();
    state.batchDownloadVisible.clear();
    state.batchDownloadPrefix = "";
    state.batchDownloadNextToken = null;
    state.batchDownloadSearchQuery = "";
    hideInline("#batch-selection-message");
    renderBatchSelection();
  }

  async function openBatchDownloadDialog() {
    if (!state.bucket) return;
    resetBatchDownloadState();
    state.batchDownloadPrefix = state.prefix;
    $("#batch-search-input").value = "";
    renderBatchBreadcrumbs();
    $("#batch-selection-dialog").showModal();
    await loadBatchObjects(false);
  }

  function renderBatchBreadcrumbs() {
    const nav = $("#batch-breadcrumbs");
    nav.innerHTML = "";
    const bucketButton = document.createElement("button");
    bucketButton.type = "button";
    bucketButton.textContent = state.bucket;
    bucketButton.addEventListener("click", () => openBatchFolder(""));
    nav.append(bucketButton);
    const parts = state.batchDownloadPrefix.split("/").filter(Boolean);
    let accumulated = "";
    parts.forEach((part) => {
      accumulated += `${part}/`;
      nav.append(separator());
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = part;
      const target = accumulated;
      button.addEventListener("click", () => openBatchFolder(target));
      nav.append(button);
    });
  }

  async function openBatchFolder(prefix) {
    state.batchDownloadPrefix = prefix;
    state.batchDownloadSearchQuery = "";
    state.batchDownloadNextToken = null;
    $("#batch-search-input").value = "";
    $("#clear-batch-search").classList.add("hidden");
    renderBatchBreadcrumbs();
    await loadBatchObjects(false);
  }

  async function loadBatchObjects(append = false) {
    const rows = $("#batch-object-rows");
    const requestId = ++state.batchDownloadRequestId;
    if (!append) {
      rows.innerHTML = '<tr><td colspan="4">読み込み中…</td></tr>';
      state.batchDownloadVisible.clear();
      $("#batch-browser-empty").classList.add("hidden");
    }
    const searching = Boolean(state.batchDownloadSearchQuery);
    const params = new URLSearchParams({ bucket: state.bucket });
    let path = "/api/objects";
    if (searching) {
      path = "/api/objects/search";
      params.set("query", state.batchDownloadSearchQuery);
    } else {
      params.set("prefix", state.batchDownloadPrefix);
    }
    if (append && state.batchDownloadNextToken) {
      params.set("continuation_token", state.batchDownloadNextToken);
    }
    try {
      const result = await api(`${path}?${params}`);
      if (requestId !== state.batchDownloadRequestId) return;
      if (!append) rows.innerHTML = "";
      if (!searching) {
        for (const folder of result.folders) rows.append(batchFolderRow(folder));
      }
      for (const object of result.objects) {
        state.batchDownloadVisible.set(object.key, object);
        rows.append(batchObjectRow(object, searching));
      }
      state.batchDownloadNextToken = result.next_token;
      $("#batch-load-more").classList.toggle("hidden", !state.batchDownloadNextToken);
      $("#batch-browser-empty").classList.toggle("hidden", rows.children.length !== 0);
      $("#batch-browser-description").textContent = searching
        ? `「${state.batchDownloadSearchQuery}」の検索結果（バケット全体）`
        : "現在のフォルダ";
      $("#clear-batch-search").classList.toggle("hidden", !searching);
    } catch (error) {
      if (requestId !== state.batchDownloadRequestId) return;
      if (!append) rows.innerHTML = `<tr><td colspan="4" class="status-error">${escapeHtml(error.message)}</td></tr>`;
      else toast(error.message, "error");
    }
  }

  function batchFolderRow(folder) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td></td><td><button type="button" class="row-link"><span class="row-name"><span class="folder-icon">□</span>${escapeHtml(folder.name)}/</span></button></td>
      <td>--</td><td>--</td>`;
    row.querySelector("button").addEventListener("click", () => openBatchFolder(folder.prefix));
    return row;
  }

  function batchObjectRow(object, searching) {
    const row = document.createElement("tr");
    row.dataset.batchKey = object.key;
    const pathDetail = searching || object.key !== object.name
      ? `<small title="${escapeHtml(object.key)}">${escapeHtml(object.key)}</small>`
      : "";
    row.innerHTML = `
      <td class="check-column"><input type="checkbox" aria-label="${escapeHtml(object.name)}を一括ダウンロード対象に選択"></td>
      <td class="batch-object-cell"><strong title="${escapeHtml(object.name)}">${escapeHtml(object.name)}</strong>${pathDetail}</td>
      <td>${formatBytes(object.size)}</td><td>${formatDate(object.last_modified)}</td>`;
    const checkbox = row.querySelector('input[type="checkbox"]');
    checkbox.checked = state.batchDownloadSelected.has(object.key);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked && !addSelection(state.batchDownloadSelected, object)) {
        checkbox.checked = false;
        toast("一度に生成できるダウンロードURLは500件までです。", "error");
      } else if (!checkbox.checked) {
        state.batchDownloadSelected.delete(object.key);
      }
      renderBatchSelection();
    });
    return row;
  }

  function syncBatchCheckboxes() {
    document.querySelectorAll("#batch-object-rows tr[data-batch-key]").forEach((row) => {
      row.querySelector('input[type="checkbox"]').checked = state.batchDownloadSelected.has(row.dataset.batchKey);
    });
  }

  function renderBatchSelection() {
    const selected = [...state.batchDownloadSelected.values()];
    $("#batch-selected-count").textContent = `${selected.length} / 500件`;
    $("#clear-batch-selection").disabled = selected.length === 0;
    const generate = $("#generate-batch-download");
    generate.disabled = selected.length === 0;
    generate.textContent = selected.length === 0 ? "URLを生成" : `${selected.length}件のURLを生成`;
    const list = $("#batch-selected-list");
    list.innerHTML = "";
    if (selected.length === 0) {
      list.innerHTML = '<div class="batch-empty">ファイルが選択されていません。</div>';
      syncBatchCheckboxes();
      return;
    }
    for (const object of selected) {
      const item = document.createElement("div");
      item.className = "batch-selected-item";
      const name = object.key.split("/").pop() || object.name || "download";
      item.innerHTML = `
        <div class="batch-selected-info"><strong title="${escapeHtml(name)}">${escapeHtml(name)}</strong><small title="${escapeHtml(object.key)}">${escapeHtml(object.key)}・${formatBytes(object.size)}</small></div>
        <button type="button" class="remove-candidate" aria-label="${escapeHtml(name)}の選択を解除">×</button>`;
      item.querySelector("button").addEventListener("click", () => {
        state.batchDownloadSelected.delete(object.key);
        renderBatchSelection();
      });
      list.append(item);
    }
    syncBatchCheckboxes();
  }

  function selectVisibleBatchObjects() {
    const result = selectVisible(state.batchDownloadSelected, [...state.batchDownloadVisible.values()]);
    renderBatchSelection();
    if (result.limitReached) toast("一度に生成できるダウンロードURLは500件までです。", "error");
  }

  async function searchBatchObjects(event) {
    event.preventDefault();
    const query = $("#batch-search-input").value.trim();
    if (!query) {
      await clearBatchSearch();
      return;
    }
    state.batchDownloadSearchQuery = query;
    state.batchDownloadNextToken = null;
    await loadBatchObjects(false);
  }

  async function clearBatchSearch() {
    state.batchDownloadSearchQuery = "";
    state.batchDownloadNextToken = null;
    $("#batch-search-input").value = "";
    $("#clear-batch-search").classList.add("hidden");
    await loadBatchObjects(false);
  }

  function showBatchResultTab(tab) {
    state.batchDownloadTab = tab;
    document.querySelectorAll(".batch-result-tab").forEach((button) => {
      const active = button.dataset.batchTab === tab;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    $("#batch-result-content").textContent = state.batchDownloadOutputValues?.[tab] || "";
  }

  async function generateBatchDownloadInfo() {
    if (!state.bucket || state.batchDownloadSelected.size === 0) return;
    const button = $("#generate-batch-download");
    const keys = [...state.batchDownloadSelected.keys()];
    try {
      setBusy(button, true, "URLを生成中…");
      const result = await api("/api/objects/download-info-batch", {
        method: "POST",
        data: { bucket: state.bucket, keys },
      });
      state.batchDownloadOutputValues = buildDownloadOutputs(result.downloads);
      const publicUrls = result.downloads.every((item) => item.public);
      const expiresIn = result.downloads.find((item) => !item.public)?.expires_in;
      $("#batch-download-count").textContent = `${result.downloads.length}件のファイル`;
      $("#batch-download-url-kind").textContent = publicUrls
        ? "公開URL"
        : `署名URL（${Math.round(expiresIn / 60)}分有効）`;
      $("#batch-download-url-kind").classList.toggle("configured", publicUrls);
      $("#batch-download-url-kind").classList.toggle("loaded", !publicUrls);
      $("#batch-download-note").textContent = publicUrls
        ? "Public URL設定から生成した期限のないURLです。"
        : "URLには認証情報が含まれます。有効期限内は第三者に共有しないでください。";
      showBatchResultTab("url");
      $("#batch-selection-dialog").close();
      $("#batch-download-dialog").showModal();
    } catch (error) {
      showInline("#batch-selection-message", error.message);
    } finally {
      setBusy(button, false);
    }
  }

  function normalizeObjectKey(rawPath) {
    let path = String(rawPath || "").trim().replaceAll("\\", "/");
    path = path.replace(/^\/+/, "").replace(/\/{2,}/g, "/");
    const segments = path.split("/");
    if (!path || path.endsWith("/")) throw new Error("移動先にはファイル名まで入力してください。");
    if (segments.some((segment) => !segment || segment === "." || segment === "..")) {
      throw new Error("移動先のパスに空の区切り、.、.. は使用できません。");
    }
    if (new TextEncoder().encode(path).length > 1024) {
      throw new Error("移動先のパスが1,024バイトを超えています。");
    }
    return path;
  }

  function openMoveDialog() {
    const object = state.actionObject;
    if (!object) return;
    $("#object-actions-dialog").close();
    $("#move-source-key").textContent = object.key;
    $("#move-destination-key").value = object.key;
    $("#move-overwrite").checked = false;
    hideInline("#move-message");
    $("#move-dialog").showModal();
    $("#move-destination-key").focus();
    $("#move-destination-key").select();
  }

  async function moveObject(event) {
    event.preventDefault();
    const object = state.actionObject;
    if (!object || !state.bucket) return;
    const button = $("#confirm-move");
    hideInline("#move-message");
    let destinationKey;
    try {
      destinationKey = normalizeObjectKey($("#move-destination-key").value);
      if (destinationKey === object.key) throw new Error("移動先は現在のパスと異なるパスを指定してください。");
      setBusy(button, true, "移動中…");
      const job = await api("/api/objects/move", {
        method: "POST",
        data: {
          bucket: state.bucket,
          source_key: object.key,
          destination_key: destinationKey,
          overwrite: $("#move-overwrite").checked,
        },
      });
      state.selected.delete(object.key);
      updateSelectionControls();
      $("#move-dialog").close();
      state.actionObject = null;
      registerMoveJob(job);
      toast("移動を開始しました。進捗は右下の転送状況で確認できます。");
    } catch (error) {
      showInline("#move-message", error.message);
    } finally {
      setBusy(button, false);
    }
  }

  async function deleteActionObject() {
    const object = state.actionObject;
    if (!object) return;
    $("#object-actions-dialog").close();
    const confirmed = await confirmAction("ファイルを削除", `「${object.name}」を削除しますか？\nこの操作は取り消せません。`);
    if (confirmed) await deleteObjectKeys([object.key]);
  }

  function updateSelectionControls() {
    const hasSelection = state.selected.size > 0;
    const deleteButton = $("#delete-selected");
    deleteButton.classList.toggle("hidden", !hasSelection);
    deleteButton.textContent = `選択した${state.selected.size}件を削除`;
  }

  async function deleteObjectKeys(keys) {
    try {
      const result = await api("/api/objects/delete", { method: "POST", data: { bucket: state.bucket, keys } });
      state.selected.clear();
      updateSelectionControls();
      if (result.errors.length) toast(`${result.deleted.length}件成功、${result.errors.length}件失敗しました。`, "error");
      else toast(`${result.deleted.length}件を削除しました。`);
      await loadObjects(false);
      if (result.deleted.length) scheduleMetricsRefresh();
    } catch (error) { toast(error.message, "error"); }
  }

  async function deleteSelected() {
    const keys = [...state.selected];
    const confirmed = await confirmAction("選択したファイルを削除", `選択した${keys.length}件を削除しますか？\nこの操作は取り消せません。`);
    if (confirmed) await deleteObjectKeys(keys);
  }

  function normalizeUploadPath(rawPath) {
    let path = String(rawPath || "").trim().replaceAll("\\", "/");
    path = path.replace(/^\/+/, "").replace(/\/{2,}/g, "/");
    if (!path) return "";
    const segments = path.split("/").filter(Boolean);
    if (segments.some((segment) => segment === "." || segment === "..")) {
      throw new Error("保存先に . または .. は使用できません。");
    }
    return `${segments.join("/")}/`;
  }

  function openUploadDialog(files = []) {
    state.pendingFiles = [];
    $("#upload-bucket-name").textContent = state.bucket;
    $("#upload-path").value = state.prefix;
    hideInline("#upload-message");
    addCandidateFiles(files);
    $("#upload-dialog").showModal();
  }

  function addCandidateFiles(fileList) {
    const byName = new Map(state.pendingFiles.map((file) => [file.name, file]));
    for (const file of [...fileList]) byName.set(file.name, file);
    state.pendingFiles = [...byName.values()];
    renderCandidates();
    $("#file-input").value = "";
  }

  function renderCandidates() {
    const container = $("#upload-candidates");
    const totalSize = state.pendingFiles.reduce((sum, file) => sum + file.size, 0);
    $("#candidate-summary").textContent = `${state.pendingFiles.length}件・${formatBytes(totalSize)}`;
    $("#start-upload").disabled = state.pendingFiles.length === 0;
    $("#clear-upload-files").disabled = state.pendingFiles.length === 0;
    container.innerHTML = "";
    if (state.pendingFiles.length === 0) {
      container.innerHTML = '<div class="candidate-empty">ファイルが追加されていません。</div>';
      return;
    }
    state.pendingFiles.forEach((file, index) => {
      const row = document.createElement("div");
      row.className = "candidate-row";
      row.innerHTML = `<span class="file-icon">▤</span><div class="candidate-info"><strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong><small>${formatBytes(file.size)}${file.type ? `・${escapeHtml(file.type)}` : ""}</small></div><button type="button" class="remove-candidate" aria-label="${escapeHtml(file.name)}を候補から削除">×</button>`;
      row.querySelector(".remove-candidate").addEventListener("click", () => {
        state.pendingFiles.splice(index, 1);
        renderCandidates();
      });
      container.append(row);
    });
  }

  function submitUploadCandidates(event) {
    event.preventDefault();
    hideInline("#upload-message");
    let destinationPrefix;
    try { destinationPrefix = normalizeUploadPath($("#upload-path").value); }
    catch (error) { showInline("#upload-message", error.message); return; }
    if (state.pendingFiles.length === 0) {
      showInline("#upload-message", "アップロードするファイルを追加してください。");
      return;
    }
    const files = [...state.pendingFiles];
    $("#upload-dialog").close();
    startFiles(files, destinationPrefix);
  }

  async function startFiles(fileList, destinationPrefix) {
    const files = [...fileList];
    for (const file of files) {
      let overwrite = false;
      let session;
      const data = {
        bucket: state.bucket,
        key: `${destinationPrefix}${file.name}`,
        file_name: file.name,
        size: file.size,
        content_type: file.type || "application/octet-stream",
      };
      try {
        session = await api("/api/uploads", { method: "POST", data });
      } catch (error) {
        if (error.code !== "OBJECT_EXISTS") { toast(`${file.name}: ${error.message}`, "error"); continue; }
        overwrite = await confirmAction("同名ファイルがあります", `「${file.name}」を上書きしますか？`, "上書き");
        if (!overwrite) continue;
        try { session = await api("/api/uploads", { method: "POST", data: { ...data, overwrite: true } }); }
        catch (secondError) { toast(`${file.name}: ${secondError.message}`, "error"); continue; }
      }
      uploadFile(session, file);
    }
  }

  function makeTransfer(session, file = null, status = "再開待ち") {
    const completed = new Map();
    for (const partNumber of Object.keys(session.parts || {})) {
      const number = Number(partNumber);
      completed.set(number, expectedPartSize(session, number));
    }
    const transfer = {
      session, file, status, completed, inFlight: new Map(), xhrs: new Set(),
      startedAt: performance.now(), initialBytes: [...completed.values()].reduce((a, b) => a + b, 0),
      cancelled: false, paused: false, error: "",
    };
    state.transfers.set(session.id, transfer);
    renderTransfers();
    return transfer;
  }

  function expectedPartSize(session, number) {
    const offset = (number - 1) * session.part_size;
    return Math.min(session.part_size, session.size - offset);
  }

  async function uploadFile(session, file) {
    const existing = state.transfers.get(session.id);
    const transfer = existing || makeTransfer(session, file, "アップロード中");
    transfer.file = file;
    transfer.status = "アップロード中";
    transfer.error = "";
    transfer.cancelled = false;
    transfer.paused = false;
    transfer.startedAt = performance.now();
    transfer.initialBytes = [...transfer.completed.values()].reduce((a, b) => a + b, 0);
    renderTransfers();
    try {
      if (file.name !== session.file_name || file.size !== session.size) throw new Error("元と同じ名前・サイズのファイルを選択してください。");
      if (session.size > 0) {
        const partCount = Math.ceil(session.size / session.part_size);
        const queue = [];
        for (let number = 1; number <= partCount; number += 1) if (!transfer.completed.has(number)) queue.push(number);
        let cursor = 0;
        let failure = null;
        const worker = async () => {
          while (cursor < queue.length && !failure && !transfer.cancelled && !transfer.paused) {
            const number = queue[cursor++];
            try { await uploadPartWithRetry(transfer, number); }
            catch (error) {
              failure = error;
              for (const xhr of transfer.xhrs) xhr.abort();
            }
          }
        };
        await Promise.all(Array.from({ length: Math.min(3, queue.length) }, worker));
        if (failure) throw failure;
        if (transfer.cancelled) return;
        if (transfer.paused) return;
      }
      await api(`/api/uploads/${session.id}/complete`, { method: "POST", data: {} });
      transfer.status = "完了";
      transfer.completed.set(1, session.size);
      transfer.inFlight.clear();
      renderTransfers();
      toast(`${session.file_name}をアップロードしました。`);
      if (state.bucket === session.bucket) await loadObjects(false);
      scheduleMetricsRefresh();
      setTimeout(() => { state.transfers.delete(session.id); renderTransfers(); }, 6000);
    } catch (error) {
      if (transfer.cancelled) return;
      transfer.status = "失敗";
      transfer.error = error.message || "アップロードに失敗しました。";
      transfer.inFlight.clear();
      renderTransfers();
    }
  }

  async function uploadPartWithRetry(transfer, number) {
    let lastError;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      if (transfer.cancelled) return;
      transfer.inFlight.set(number, 0);
      renderTransfers();
      const start = (number - 1) * transfer.session.part_size;
      const end = Math.min(start + transfer.session.part_size, transfer.file.size);
      try {
        await uploadPartRequest(transfer, number, transfer.file.slice(start, end));
        transfer.inFlight.delete(number);
        transfer.completed.set(number, end - start);
        renderTransfers();
        return;
      } catch (error) {
        transfer.inFlight.delete(number);
        if (transfer.cancelled || error.name === "AbortError") return;
        lastError = error;
        if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, 700 * (2 ** (attempt - 1))));
      }
    }
    throw lastError;
  }

  function uploadPartRequest(transfer, number, blob) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      transfer.xhrs.add(xhr);
      xhr.open("PUT", `/api/uploads/${transfer.session.id}/parts/${number}`);
      xhr.setRequestHeader("X-R2FM-Token", token);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) transfer.inFlight.set(number, event.loaded);
      };
      xhr.onload = () => {
        transfer.xhrs.delete(xhr);
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else {
          let message = "パートの送信に失敗しました。";
          try { message = JSON.parse(xhr.responseText).error || message; } catch (_) { /* no-op */ }
          reject(new Error(message));
        }
      };
      xhr.onerror = () => { transfer.xhrs.delete(xhr); reject(new Error("ネットワークエラーが発生しました。")); };
      xhr.onabort = () => { transfer.xhrs.delete(xhr); const error = new Error("中止しました。"); error.name = "AbortError"; reject(error); };
      xhr.send(blob);
    });
  }

  function transferBytes(transfer) {
    return [...transfer.completed.values(), ...transfer.inFlight.values()].reduce((a, b) => a + b, 0);
  }

  function renderTransfers() {
    const center = $("#transfer-center");
    const list = $("#transfer-list");
    const transfers = [...state.transfers.values()];
    const moveJobs = [...state.moveJobs.values()];
    center.classList.toggle("hidden", transfers.length === 0 && moveJobs.length === 0);
    list.innerHTML = "";
    for (const transfer of transfers) {
      const bytes = Math.min(transferBytes(transfer), transfer.session.size);
      const percent = transfer.session.size === 0 ? (transfer.status === "完了" ? 100 : 0) : (bytes / transfer.session.size) * 100;
      const elapsed = Math.max((performance.now() - transfer.startedAt) / 1000, .1);
      const speed = Math.max(bytes - transfer.initialBytes, 0) / elapsed;
      const remaining = speed > 0 ? (transfer.session.size - bytes) / speed : Infinity;
      const item = document.createElement("div");
      item.className = "transfer-item";
      const statusClass = transfer.status === "失敗" ? "status-error" : transfer.status === "完了" ? "status-complete" : "";
      const canResume = ["失敗", "再開待ち", "一時停止"].includes(transfer.status);
      item.innerHTML = `
        <div class="transfer-title-row"><span class="transfer-name" title="${escapeHtml(transfer.session.file_name)}">${escapeHtml(transfer.session.file_name)}</span><span class="${statusClass}">${escapeHtml(transfer.status)}</span></div>
        <div class="progress-track"><div class="progress-bar" style="width:${percent.toFixed(2)}%"></div></div>
        <div class="transfer-meta"><span>${formatBytes(bytes)} / ${formatBytes(transfer.session.size)}</span><span>${speed > 0 && transfer.status === "アップロード中" ? `${formatBytes(speed)}/s・${formatDuration(remaining)}` : ""}</span></div>
        ${transfer.error ? `<div class="transfer-meta status-error">${escapeHtml(transfer.error)}</div>` : ""}
        <div class="transfer-actions">${transfer.status === "アップロード中" ? '<button class="pause-upload">一時停止</button>' : ""}${canResume ? `<button class="resume-upload">${transfer.file ? "再開" : "ファイルを選択して再開"}</button>` : ""}${transfer.status !== "完了" ? '<button class="cancel-upload">キャンセル</button>' : ""}</div>`;
      item.querySelector(".pause-upload")?.addEventListener("click", () => pauseUpload(transfer));
      item.querySelector(".resume-upload")?.addEventListener("click", () => transfer.file ? uploadFile(transfer.session, transfer.file) : chooseResumeFile(transfer));
      item.querySelector(".cancel-upload")?.addEventListener("click", () => cancelUpload(transfer));
      list.append(item);
    }
    for (const job of moveJobs) {
      const bytes = Math.min(job.transferred_bytes || 0, job.total_bytes || 0);
      const percent = job.status === "complete" ? 100 : job.total_bytes > 0 ? (bytes / job.total_bytes) * 100 : 0;
      const labels = { queued: "準備中", moving: "移動中", complete: "完了", failed: "失敗" };
      const item = document.createElement("div");
      item.className = "transfer-item";
      const statusClass = job.status === "failed" ? "status-error" : job.status === "complete" ? "status-complete" : "";
      const title = `${job.source_key} → ${job.destination_key}`;
      const sizeText = job.total_bytes > 0
        ? `${formatBytes(bytes)} / ${formatBytes(job.total_bytes)}（${Math.floor(percent)}%）`
        : job.status === "complete" ? "0 B / 0 B（100%）" : "サイズを確認中";
      item.innerHTML = `
        <div class="transfer-title-row"><span class="transfer-name" title="${escapeHtml(title)}">移動: ${escapeHtml(job.destination_key)}</span><span class="${statusClass}">${labels[job.status] || escapeHtml(job.status)}</span></div>
        <div class="progress-track"><div class="progress-bar" style="width:${percent.toFixed(2)}%"></div></div>
        <div class="transfer-meta"><span>${sizeText}</span><span></span></div>
        ${job.error ? `<div class="transfer-meta status-error">${escapeHtml(job.error)}</div>` : ""}`;
      list.append(item);
    }
  }

  function registerMoveJob(job) {
    const existing = state.moveJobs.get(job.id);
    state.moveJobs.set(job.id, { ...existing, ...job });
    renderTransfers();
    if (!["complete", "failed"].includes(job.status) && !existing?.polling) pollMoveJob(job.id);
  }

  async function pollMoveJob(jobId) {
    const initial = state.moveJobs.get(jobId);
    if (!initial) return;
    initial.polling = true;
    while (state.moveJobs.has(jobId)) {
      await new Promise((resolve) => setTimeout(resolve, 750));
      try {
        const latest = await api(`/api/moves/${jobId}`);
        const job = state.moveJobs.get(jobId);
        if (!job) return;
        Object.assign(job, latest, { polling: true });
        renderTransfers();
        if (latest.status === "complete") {
          toast(`「${latest.destination_key}」へ移動しました。`);
          if (state.bucket === latest.bucket) await loadObjects(false);
          scheduleMetricsRefresh();
          setTimeout(() => { state.moveJobs.delete(jobId); renderTransfers(); }, 6000);
          return;
        }
        if (latest.status === "failed") {
          toast(latest.error || "ファイルの移動に失敗しました。", "error");
          return;
        }
      } catch (error) {
        const job = state.moveJobs.get(jobId);
        if (!job) return;
        job.error = `進捗を取得できません: ${error.message}`;
        renderTransfers();
        if (error.status === 404) return;
      }
    }
  }

  function chooseResumeFile(transfer) {
    const input = document.createElement("input");
    input.type = "file";
    input.addEventListener("change", () => { if (input.files[0]) uploadFile(transfer.session, input.files[0]); });
    input.click();
  }

  function pauseUpload(transfer) {
    transfer.paused = true;
    transfer.status = "一時停止";
    transfer.inFlight.clear();
    for (const xhr of transfer.xhrs) xhr.abort();
    renderTransfers();
  }

  async function cancelUpload(transfer) {
    transfer.cancelled = true;
    for (const xhr of transfer.xhrs) xhr.abort();
    transfer.status = "キャンセル中";
    renderTransfers();
    try {
      await api(`/api/uploads/${transfer.session.id}`, { method: "DELETE" });
      state.transfers.delete(transfer.session.id);
      renderTransfers();
      toast("アップロードをキャンセルしました。");
      scheduleMetricsRefresh();
    } catch (error) {
      transfer.status = "失敗";
      transfer.error = error.message;
      renderTransfers();
    }
  }

  async function loadIncompleteUploads() {
    try {
      const { uploads } = await api("/api/uploads");
      for (const session of uploads) if (!state.transfers.has(session.id)) makeTransfer(session);
    } catch (error) { toast(error.message, "error"); }
  }

  async function loadMoveJobs() {
    try {
      const { moves } = await api("/api/moves");
      for (const job of moves) registerMoveJob(job);
    } catch (error) { toast(error.message, "error"); }
  }

  function bindEvents() {
    $("#settings-button").addEventListener("click", openSettings);
    $("#configure-metrics").addEventListener("click", openSettings);
    document.querySelectorAll(".modal-close, .dialog-close-button").forEach((button) => button.addEventListener("click", () => {
      const dialog = button.closest("dialog");
      if (dialog.dataset.required !== "true") dialog.close();
    }));
    $("#test-settings").addEventListener("click", testSettings);
    $("#save-settings").addEventListener("click", saveSettings);
    $("#load-environment").addEventListener("click", loadEnvironment);
    $("#toggle-secret").addEventListener("click", () => {
      const input = $("#settings-form").elements.secret_access_key;
      input.type = input.type === "password" ? "text" : "password";
      $("#toggle-secret").textContent = input.type === "password" ? "表示" : "隠す";
    });
    $("#toggle-api-token").addEventListener("click", () => {
      const input = $("#settings-form").elements.cloudflare_api_token;
      input.type = input.type === "password" ? "text" : "password";
      $("#toggle-api-token").textContent = input.type === "password" ? "表示" : "隠す";
    });
    $("#refresh-buckets").addEventListener("click", () => Promise.all([loadBuckets(), loadMetrics()]));
    $("#create-bucket-button").addEventListener("click", () => { $("#bucket-form").reset(); hideInline("#bucket-message"); $("#bucket-dialog").showModal(); });
    document.querySelectorAll(".create-bucket-shortcut").forEach((button) => button.addEventListener("click", () => $("#create-bucket-button").click()));
    $("#bucket-form").addEventListener("submit", createBucket);
    $("#refresh-objects").addEventListener("click", () => loadObjects(false));
    $("#upload-button").addEventListener("click", () => openUploadDialog());
    $("#choose-upload-files").addEventListener("click", () => $("#file-input").click());
    $("#file-input").addEventListener("change", (event) => addCandidateFiles(event.target.files));
    $("#upload-form").addEventListener("submit", submitUploadCandidates);
    $("#clear-upload-files").addEventListener("click", () => { state.pendingFiles = []; renderCandidates(); });
    const uploadDropArea = $("#upload-drop-area");
    uploadDropArea.addEventListener("click", (event) => { if (event.target === uploadDropArea) $("#file-input").click(); });
    uploadDropArea.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); $("#file-input").click(); }
    });
    uploadDropArea.addEventListener("dragenter", (event) => { event.preventDefault(); uploadDropArea.classList.add("dragging"); });
    uploadDropArea.addEventListener("dragover", (event) => event.preventDefault());
    uploadDropArea.addEventListener("dragleave", () => uploadDropArea.classList.remove("dragging"));
    uploadDropArea.addEventListener("drop", (event) => { event.preventDefault(); uploadDropArea.classList.remove("dragging"); addCandidateFiles(event.dataTransfer.files); });
    $("#load-more").addEventListener("click", () => loadObjects(true));
    $("#open-batch-download").addEventListener("click", openBatchDownloadDialog);
    $("#batch-search-form").addEventListener("submit", searchBatchObjects);
    $("#clear-batch-search").addEventListener("click", clearBatchSearch);
    $("#batch-load-more").addEventListener("click", () => loadBatchObjects(true));
    $("#select-visible-batch").addEventListener("click", selectVisibleBatchObjects);
    $("#clear-batch-selection").addEventListener("click", () => {
      state.batchDownloadSelected.clear();
      renderBatchSelection();
    });
    $("#generate-batch-download").addEventListener("click", generateBatchDownloadInfo);
    $("#batch-selection-dialog").addEventListener("close", resetBatchDownloadState);
    document.querySelectorAll(".batch-result-tab").forEach((button) => button.addEventListener("click", () => {
      showBatchResultTab(button.dataset.batchTab);
    }));
    $("#copy-batch-result").addEventListener("click", () => {
      copyText(state.batchDownloadOutputValues?.[state.batchDownloadTab] || "");
    });
    $("#delete-selected").addEventListener("click", deleteSelected);
    $("#download-object").addEventListener("click", downloadObject);
    $("#move-object").addEventListener("click", openMoveDialog);
    $("#move-form").addEventListener("submit", moveObject);
    $("#show-download-info").addEventListener("click", showDownloadInfo);
    $("#delete-object").addEventListener("click", deleteActionObject);
    document.querySelectorAll(".copy-button").forEach((button) => button.addEventListener("click", () => {
      const target = $(`#${button.dataset.copyTarget}`);
      copyText(target.value ?? target.textContent);
    }));
    $("#select-all").addEventListener("change", (event) => {
      document.querySelectorAll('#object-rows tr[data-key] input[type="checkbox"]').forEach((checkbox) => {
        checkbox.checked = event.target.checked;
        const key = checkbox.closest("tr").dataset.key;
        if (event.target.checked) state.selected.add(key); else state.selected.delete(key);
      });
      updateSelectionControls();
    });
    let dragDepth = 0;
    const zone = $("#drop-zone");
    zone.addEventListener("dragenter", (event) => { event.preventDefault(); dragDepth += 1; $("#drop-overlay").classList.remove("hidden"); });
    zone.addEventListener("dragover", (event) => event.preventDefault());
    zone.addEventListener("dragleave", () => { dragDepth -= 1; if (dragDepth <= 0) { dragDepth = 0; $("#drop-overlay").classList.add("hidden"); } });
    zone.addEventListener("drop", (event) => { event.preventDefault(); dragDepth = 0; $("#drop-overlay").classList.add("hidden"); openUploadDialog(event.dataTransfer.files); });
    $("#toggle-transfers").addEventListener("click", () => {
      $("#transfer-center").classList.toggle("collapsed");
      $("#toggle-transfers").textContent = $("#transfer-center").classList.contains("collapsed") ? "+" : "−";
    });
  }

  bindEvents();
  setInterval(() => { if ([...state.transfers.values()].some((item) => item.status === "アップロード中")) renderTransfers(); }, 1000);
  loadInitialSettings().catch((error) => toast(error.message, "error"));
})();
