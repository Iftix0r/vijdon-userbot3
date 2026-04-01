module.exports = {
	"apps" : [
		{
			"name" : "vijdon3-bot",
			"script" : "bot.py",
			"interpreter" : "python3",
			"cwd" : __dirname,
			"watch" : false,
			"autorestart" : true,
			"max_restarts" : 10,
			"restart_delay" : 5000
		},
		{
			"name" : "vijdon3-main",
			"script" : "main.py",
			"interpreter" : "python3",
			"cwd" : __dirname,
			"watch" : false,
			"autorestart" : true,
			"max_restarts" : 10,
			"restart_delay" : 5000
		}
	]
}
