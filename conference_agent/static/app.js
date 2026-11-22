// Upload drop zone: show the chosen file name and accept drag-and-drop.
(function () {
  const drop = document.getElementById("drop");
  const input = document.getElementById("paper");
  const text = document.getElementById("drop-text");
  if (!drop || !input) return;
  const show = () => {
    const file = input.files && input.files[0];
    if (!file) return;
    drop.classList.add("has-file");
    text.textContent = file.name + " · " + (file.size / 1048576).toFixed(1) + " MB";
  };
  input.addEventListener("change", show);
  ["dragenter", "dragover"].forEach((e) =>
    drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); })
  );
  ["dragleave", "drop"].forEach((e) =>
    drop.addEventListener(e, () => drop.classList.remove("over"))
  );
  drop.addEventListener("drop", (ev) => {
    ev.preventDefault();
    if (ev.dataTransfer && ev.dataTransfer.files.length) {
      input.files = ev.dataTransfer.files;
      show();
    }
  });
  const form = drop.closest("form");
  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    button.textContent = "Uploading…";
  });
})();

// Progress page: poll the run's status, update counts, reload when finished.
(function () {
  const box = document.getElementById("progress");
  if (!box) return;
  const url = box.dataset.statusUrl;
  let elapsed = parseInt(box.dataset.started || "0", 10);
  const $ = (id) => document.getElementById(id);
  const tick = () => {
    elapsed += 1;
    $("elapsed").textContent = Math.floor(elapsed / 60) + ":" + String(elapsed % 60).padStart(2, "0");
  };
  tick();
  setInterval(tick, 1000);
  const poll = async () => {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (res.ok) {
        const s = await res.json();
        if (s.state === "done" || s.state === "failed") return location.reload();
        $("searches").textContent = s.searches;
        $("pages").textContent = s.pages;
        $("bar").style.width = Math.round((s.pages / s.max_pages) * 100) + "%";
        $("phase").textContent =
          s.state === "queued" ? "Waiting for " + s.ahead + " earlier run" + (s.ahead === 1 ? "" : "s") + " to finish"
          : s.pages ? "Reading and checking pages"
          : s.searches ? "Searching the web"
          : "Reading your paper";
      }
    } catch (_) { /* server restarting; try again */ }
    setTimeout(poll, 2500);
  };
  setTimeout(poll, 2500);
})();
