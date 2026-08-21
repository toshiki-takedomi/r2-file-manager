"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  addSelection,
  buildDownloadOutputs,
  selectVisible,
  uniqueDownloadNames,
} = require("../r2_file_manager/static/download_utils.js");

test("URL, curl, wget use the same URLs and collision-free output names", () => {
  const downloads = [
    { key: "models/a/model.bin", url: "https://example.test/a?sig=one" },
    { key: "models/b/model.bin", url: "https://example.test/b?sig=two" },
    { key: "models/c/model.bin", url: "https://example.test/c?sig=three" },
  ];

  assert.deepEqual(uniqueDownloadNames(downloads.map((item) => item.key)), [
    "model.bin",
    "model (2).bin",
    "model (3).bin",
  ]);
  assert.deepEqual(buildDownloadOutputs(downloads), {
    url: "https://example.test/a?sig=one\nhttps://example.test/b?sig=two\nhttps://example.test/c?sig=three",
    curl: [
      "curl --fail --location --output 'model.bin' 'https://example.test/a?sig=one'",
      "curl --fail --location --output 'model (2).bin' 'https://example.test/b?sig=two'",
      "curl --fail --location --output 'model (3).bin' 'https://example.test/c?sig=three'",
    ].join("\n"),
    wget: [
      "wget --output-document='model.bin' 'https://example.test/a?sig=one'",
      "wget --output-document='model (2).bin' 'https://example.test/b?sig=two'",
      "wget --output-document='model (3).bin' 'https://example.test/c?sig=three'",
    ].join("\n"),
  });
});

test("selection survives changing visible folders and is capped at 500", () => {
  const selected = new Map();
  assert.equal(addSelection(selected, { key: "folder-a/a.bin", size: 1 }), true);
  assert.equal(addSelection(selected, { key: "folder-b/b.bin", size: 2 }), true);
  assert.deepEqual([...selected.keys()], ["folder-a/a.bin", "folder-b/b.bin"]);

  const visible = Array.from({ length: 500 }, (_value, index) => ({
    key: `search/result-${index}.bin`,
    size: index,
  }));
  const result = selectVisible(selected, visible, 500);

  assert.equal(result.added, 498);
  assert.equal(result.limitReached, true);
  assert.equal(selected.size, 500);
  assert.equal(addSelection(selected, { key: "overflow.bin" }, 500), false);
});

test("selecting the same object twice keeps a single canonical selection", () => {
  const selected = new Map();
  const original = { key: "models/a.bin", size: 1 };
  assert.equal(addSelection(selected, original), true);
  assert.equal(addSelection(selected, { key: "models/a.bin", size: 99 }), true);
  assert.equal(selected.size, 1);
  assert.equal(selected.get("models/a.bin"), original);
});
