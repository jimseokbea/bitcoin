module.exports = {
    apps: [{
        name: "binance_bot",
        script: "main_binance.py",
        interpreter: "python3",
        watch: false,
        autorestart: true,
        restart_delay: 5000,
    }]
};
