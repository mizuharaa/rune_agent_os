// Wisp desktop shell: tray + main dashboard window + always-on-top mini bar.
// The Python engine (dashboard/serve.py) runs as a sidecar on 127.0.0.1:8817.
const { app, BrowserWindow, Tray, Menu, globalShortcut, nativeImage } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");

const REPO = path.join(__dirname, "..");
const BASE = "http://127.0.0.1:8817";
const SMOKE = process.argv.includes("--smoke");

let sidecar = null;
let mainWin = null;
let miniBar = null;
let tray = null;

function ping(cb) {
  http.get(BASE + "/dashboard/", (res) => cb(res.statusCode < 500)).on("error", () => cb(false));
}

function ensureEngine(cb) {
  ping((up) => {
    if (up) return cb();
    sidecar = spawn("python", ["dashboard/serve.py"], { cwd: REPO, stdio: "ignore" });
    const started = Date.now();
    (function wait() {
      ping((ok) => {
        if (ok) return cb();
        if (Date.now() - started > 20000) { console.error("engine did not start"); app.quit(); return; }
        setTimeout(wait, 300);
      });
    })();
  });
}

function createMainWindow() {
  mainWin = new BrowserWindow({
    width: 1480,
    height: 940,
    show: false,
    autoHideMenuBar: true,
    icon: path.join(__dirname, "icon.png"),
    backgroundColor: "#111013",
  });
  mainWin.loadURL(BASE + "/dashboard/");
  mainWin.once("ready-to-show", () => { if (!SMOKE) mainWin.show(); });
  mainWin.on("minimize", () => showMiniBar());
  mainWin.on("close", (e) => {
    // ponytail: close-to-tray; real quit only via tray menu
    if (!app.isQuittingForReal) { e.preventDefault(); mainWin.hide(); showMiniBar(); }
  });
}

function createMiniBar() {
  const { width } = require("electron").screen.getPrimaryDisplay().workAreaSize;
  miniBar = new BrowserWindow({
    width: 460,
    height: 72,
    x: Math.round((width - 460) / 2),
    y: 12,
    frame: false,
    transparent: true,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: false,
  });
  miniBar.setAlwaysOnTop(true, "screen-saver");
  // served by the python engine so fetch() to /api/* is same-origin
  miniBar.loadURL(BASE + "/app/minibar.html");
}

function showMiniBar() { if (miniBar && !miniBar.isVisible()) miniBar.showInactive(); }
function hideMiniBar() { if (miniBar && miniBar.isVisible()) miniBar.hide(); }

function restoreMain() {
  hideMiniBar();
  if (mainWin) { mainWin.show(); mainWin.focus(); }
}

function createTray() {
  tray = new Tray(nativeImage.createFromPath(path.join(__dirname, "icon.png")));
  tray.setToolTip("Wisp");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open Wisp", click: restoreMain },
    { label: "Toggle mini bar", click: () => (miniBar.isVisible() ? hideMiniBar() : showMiniBar()) },
    { type: "separator" },
    { label: "Quit", click: () => { app.isQuittingForReal = true; app.quit(); } },
  ]));
  tray.on("double-click", restoreMain);
}

app.whenReady().then(() => {
  ensureEngine(() => {
    createMainWindow();
    createMiniBar();
    createTray();
    globalShortcut.register("Control+Shift+Space", () => {
      if (mainWin.isVisible() && mainWin.isFocused()) { mainWin.hide(); showMiniBar(); }
      else restoreMain();
    });
    if (SMOKE) { console.log("SMOKE_OK"); app.isQuittingForReal = true; setTimeout(() => app.quit(), 500); }
  });
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  if (sidecar) sidecar.kill();
});
app.on("window-all-closed", () => {}); // stay in tray
