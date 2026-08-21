((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.R2DownloadUtils = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  "use strict";

  function shellQuote(value) {
    return `'${String(value).replaceAll("'", `'"'"'`)}'`;
  }

  function uniqueDownloadNames(keys) {
    const used = new Set();
    return keys.map((key) => {
      const original = key.split("/").pop() || "download";
      let candidate = original;
      let number = 2;
      while (used.has(candidate)) {
        const dot = original.lastIndexOf(".");
        const stem = dot > 0 ? original.slice(0, dot) : original;
        const suffix = dot > 0 ? original.slice(dot) : "";
        candidate = `${stem} (${number})${suffix}`;
        number += 1;
      }
      used.add(candidate);
      return candidate;
    });
  }

  function buildDownloadOutputs(downloads) {
    const names = uniqueDownloadNames(downloads.map((item) => item.key));
    return {
      url: downloads.map((item) => item.url).join("\n"),
      curl: downloads.map((item, index) => (
        `curl --fail --location --output ${shellQuote(names[index])} ${shellQuote(item.url)}`
      )).join("\n"),
      wget: downloads.map((item, index) => (
        `wget --output-document=${shellQuote(names[index])} ${shellQuote(item.url)}`
      )).join("\n"),
    };
  }

  function addSelection(selected, object, max = 500) {
    if (selected.has(object.key)) return true;
    if (selected.size >= max) return false;
    selected.set(object.key, object);
    return true;
  }

  function selectVisible(selected, objects, max = 500) {
    let added = 0;
    let limitReached = false;
    for (const object of objects) {
      if (selected.has(object.key)) continue;
      if (!addSelection(selected, object, max)) {
        limitReached = true;
        break;
      }
      added += 1;
    }
    return { added, limitReached };
  }

  return { shellQuote, uniqueDownloadNames, buildDownloadOutputs, addSelection, selectVisible };
});
