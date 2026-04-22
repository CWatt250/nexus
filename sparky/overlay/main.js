const { app, BrowserWindow, screen, ipcMain, globalShortcut, shell } = require('electron');
const path = require('path');

const WIN_WIDTH = 220;
const WIN_HEIGHT = 250;
const EDGE_MARGIN = 10;        // px — gap between the window and the screen edge
const CURSOR_POLL_MS = 50;

let mainWindow = null;
let cursorTimer = null;
let dragOrigin = null;         // { x, y } window position when a drag started

function createWindow() {
  const { width: screenWidth, height: screenHeight } = screen.getPrimaryDisplay().workAreaSize;

  mainWindow = new BrowserWindow({
    width: WIN_WIDTH,
    height: WIN_HEIGHT,
    // Spawn in the bottom-right corner of the primary display's work area
    // (respects the taskbar/panel).
    x: screenWidth - WIN_WIDTH - EDGE_MARGIN,
    y: screenHeight - WIN_HEIGHT - EDGE_MARGIN,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    hasShadow: false,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    }
  });

  mainWindow.loadFile('index.html');

  // Global cursor polling — fires even when the cursor is outside the overlay
  // window. Renderer uses this to decide whether Sparky's eyes should track.
  cursorTimer = setInterval(() => {
    if (!mainWindow || mainWindow.isDestroyed() || !mainWindow.isVisible()) return;
    try {
      const cursor = screen.getCursorScreenPoint();
      const b = mainWindow.getBounds();
      mainWindow.webContents.send('cursor-pos', {
        cursor,
        window: { x: b.x, y: b.y, width: b.width, height: b.height }
      });
    } catch (e) {
      // ignore — screen API occasionally throws on multi-monitor transitions
    }
  }, CURSOR_POLL_MS);

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.setIgnoreMouseEvents(false);
}

app.whenReady().then(() => {
  createWindow();

  // Alt+Shift+S toggles Sparky's visibility — lets the user get him back
  // after the "Hide" context-menu action.
  globalShortcut.register('Alt+Shift+S', () => {
    if (!mainWindow) {
      createWindow();
      return;
    }
    if (mainWindow.isVisible()) {
      mainWindow.hide();
    } else {
      mainWindow.show();
    }
  });
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (cursorTimer) clearInterval(cursorTimer);
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

// IPC for state updates from Nexus (kept for compatibility — the renderer
// also polls the HTTP bridge directly, so this is a fast-path).
ipcMain.on('set-state', (event, state) => {
  if (mainWindow) {
    mainWindow.webContents.send('state-update', state);
  }
});

// Context-menu actions forwarded from the renderer.
ipcMain.on('sparky-hide', () => {
  if (mainWindow) mainWindow.hide();
});

ipcMain.on('sparky-show', () => {
  if (mainWindow) mainWindow.show();
});

ipcMain.on('sparky-settings', () => {
  // Minimal "settings" — open the state bridge health endpoint in the
  // default browser. Gives the user a live view of what Sparky is seeing.
  shell.openExternal('http://localhost:11437/state');
});

// ---------------- Manual drag ----------------
// On Wayland (Ubuntu 24.04 default) Chromium's -webkit-app-region: drag
// is unreliable for transparent/frameless windows, so the renderer also
// drives drag directly via pointer events + screen coordinates.
ipcMain.on('sparky-drag-start', () => {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const b = mainWindow.getBounds();
  dragOrigin = { x: b.x, y: b.y };
});

ipcMain.on('sparky-drag-move', (event, payload) => {
  if (!dragOrigin || !mainWindow || mainWindow.isDestroyed()) return;
  const dx = (payload && typeof payload.dx === 'number') ? payload.dx : 0;
  const dy = (payload && typeof payload.dy === 'number') ? payload.dy : 0;
  mainWindow.setPosition(Math.round(dragOrigin.x + dx), Math.round(dragOrigin.y + dy));
});

ipcMain.on('sparky-drag-end', () => {
  dragOrigin = null;
});
