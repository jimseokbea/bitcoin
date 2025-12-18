module.exports = {
    apps: [{
        name: "monster_bot",
        script: "main.py",
        interpreter: "python3",
        watch: false,
        autorestart: true,
        restart_delay: 5000,
    }]
};
