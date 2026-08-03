const path = require("path");
const fs = require("fs");

const root = __dirname;
const isWin = process.platform === "win32";
const venvPython = path.join(
  root,
  ".venv",
  isWin ? "Scripts" : "bin",
  isWin ? "python.exe" : "python"
);
const interpreter = fs.existsSync(venvPython) ? venvPython : "python";

module.exports = {
  apps: [
    {
      name: "geomaps",
      script: path.join(root, "app.py"),
      interpreter,
      cwd: root,
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      watch: false,
      max_memory_restart: "512M",
      env: {
        NODE_ENV: "production",
        PYTHONUNBUFFERED: "1",
      },
      // Load .env via python-dotenv inside the app; keep PM2 env minimal.
      error_file: path.join(root, "logs", "pm2-error.log"),
      out_file: path.join(root, "logs", "pm2-out.log"),
      merge_logs: true,
      time: true,
    },
  ],
};
