// PM2 process definition for the skill-odoo Telegram receipt bot.
//
//   pm2 start ecosystem.config.js     # start (run from the repo root)
//   pm2 logs skill-odoo-bot           # tail logs
//   pm2 restart skill-odoo-bot        # restart after a code change
//   pm2 stop skill-odoo-bot           # stop
//   pm2 save                          # persist across reboots
//
// Logs are written under tmp/logs/ to keep all runtime state in one place.
module.exports = {
  apps: [
    {
      name: "skill-odoo-bot",
      cwd: __dirname,
      script: ".venv/bin/python",
      args: "-m scripts telegram-bot",
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      output: "tmp/logs/telegram-bot.log",
      error: "tmp/logs/telegram-bot.err.log",
      time: true,
    },
  ],
};
