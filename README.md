# AutoDL GPU 监控与自动开机

基于 [Jiayi-Fu/autodl-gpu-monitor](https://github.com/Jiayi-Fu/autodl-gpu-monitor) 的 Windows 本地控制台，支持监控现有实例的开机名额，以及按条件寻找新实例。界面标识：[Pig-uncle](https://github.com/Pig-uncle)。

## 功能

- 现有实例自动重试 GPU 开机；新机器按显卡型号、卡数、地区和数据盘扩容条件筛选。
- 支持只开机、只抢新机器、先开机再抢新机器，以及两路同步监控。同步模式下，一路请求被接受后停止另一侧提交。
- 微信扫码登录；手机号短信登录需要用户亲自完成 AutoDL 的人机验证。验证组件可能受平台来源限制。
- 显示账号名称、ID 和余额；支持 GPU 型号多选、地区不限或指定地区。
- 下单结果不确定时停止并保留记录，防止自动重复下单。

## Windows 直接使用

从本仓库的 Releases 下载 Windows 压缩包，解压后双击 `启动控制台.bat` 或 `AutoDL-Monitor.exe`。首次登录使用自己的 AutoDL 账号，再设置自己的实例 UUID 和抢卡条件。

启动后请保持**最初的程序窗口**打开；关闭这个窗口会停止本地监控并释放端口。只关闭浏览器页面不会停止服务。重复启动同一目录的程序会打开现有页面，仍需通过最初的窗口停止服务。

开机或租用会产生费用，扩容数据盘按 AutoDL 规则收费，关机后可能继续计费。新租实例不会自动迁移旧实例的数据。停止本地监控不会关闭云端实例或撤销已接受的请求。

## 从源码运行

需要 Python 3.8+。在项目目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

网页默认只监听本机 `127.0.0.1:8765`。运行时请保持电脑开机、联网且不休眠。服务退出后需要重新启动并手动开始抢卡。

## 打包 Windows 版本

在 Windows 中安装依赖和 PyInstaller，然后运行：

```powershell
python -m pip install -r requirements.txt pyinstaller
.\build_windows.bat
```

双击 `dist/AutoDL-Monitor/start.bat` 启动；将 `dist/AutoDL-Monitor` 整个目录打包分享；不要包含运行产生的 `.local` 或 `config.local.json`。

## 配置和隐私

登录令牌以明文保存在本机 `.local/auth.json`，请勿分享。配置保存在 `config.local.json`；这两个路径默认被 Git 忽略。检测日志与下单记录也保存在 `.local`。发布的初始配置不包含个人账号或实例 ID。

## 测试

```powershell
python -m unittest -v test_monitor.py test_phone_login.py test_share_launch.py
```

测试通过模拟接口检查自动开机、下单防重复、登录流程和启动方式；实际 AutoDL 页面接口与人机验证可能变化，真实短信登录及收费流程需结合平台状态验证。

## 来源与许可

本项目基于 [Jiayi-Fu/autodl-gpu-monitor](https://github.com/Jiayi-Fu/autodl-gpu-monitor)，保留上游提交历史。上游说明其部分代码参考了 [lemonmindyes/autogpu](https://github.com/lemonmindyes/autogpu)。上游仓库目前未声明开源许可证；本仓库未擅自添加许可证。使用或再次分发上游代码前，请自行确认相关授权。
