const { app, BrowserWindow, screen, ipcMain } = require('electron');
const path = require('path');

let mainWindow = null;

function createWindow() {
  const { width: screenWidth, height: screenHeight } = screen.getPrimaryDisplay().workAreaSize;

  mainWindow = new BrowserWindow({
    width: 220,
    height: 250,
    x: screenWidth - 240,
    y: screenHeight - 280,
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

  // Hide on double-click, show again
  let visible = true;
  mainWindow.on('dblclick', () => {
    visible = !visible;
    if (visible) {
      mainWindow.show();
    } else {
      mainWindow.hide();
    }
  });

  // Allow dragging
  mainWindow.setIgnoreMouseEvents(false);
}

app.whenReady().then(createWindow);

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

// IPC for state updates from Nexus
ipcMain.on('set-state', (event, state) => {
  if (mainWindow) {
    mainWindow.webContents.send('state-update', state);
  }
});
