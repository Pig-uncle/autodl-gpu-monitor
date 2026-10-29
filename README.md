# AutoDL GPU 监控与自动开机

基于 [Jiayi-Fu/autodl-gpu-monitor](https://github.com/Jiayi-Fu/autodl-gpu-monitor) 的 Windows 本地控制台，支持监控现有实例的开机名额，以及按条件寻找新实例。界面标识：[Pig-uncle](https://github.com/Pig-uncle)。

## 功能

- 现有实例自动重试 GPU 开机；新机器按显卡型号、卡数、计费方式、地区和数据盘扩容条件筛选。
- 支持按量、包日、包周和包月；包时按选择的租期创建预付订单，核对订单金额后支付。
- 支持一次性定时开始，按本机时区设置时间；到点时电脑与程序必须仍在运行。
- 新机器可选择克隆已有实例的系统盘，并选择是否复制数据盘。
- 支持只开机、只抢新机器、先开机再抢新机器，以及两路同步监控。同步模式下，一路请求被接受后停止另一侧提交。
- 微信扫码登录；手机号短信登录需要用户亲自完成 AutoDL 的人机验证。验证组件可能受平台来源限制。
- 显示账号名称、ID 和余额；支持 GPU 型号多选、地区不限或指定地区。
- 下单结果不确定时停止并保留记录，防止自动重复下单。

## 克隆已有实例

在“新机器创建方式”中选择“克隆已有实例”，填写克隆源 UUID，可选择是否同时复制数据盘。源实例必须先在 AutoDL 控制台关机；程序不会自行关机。系统盘始终克隆，新实例只能选择源所在区域且芯片和 CPU 架构须匹配。勾选复制数据盘时，目标额外数据盘容量不得小于源实例当前扩容容量。可以先点“检查克隆源”。

同步抢卡时，克隆与原实例开机共用防重复保护；任一请求被接受后，程序不会继续提交另一侧。克隆实例可能开始运行时数据盘仍在复制，界面显示平台返回的复制状态；请在 AutoDL 控制台确认复制是否完成。克隆过程与数据盘可能产生费用。

克隆及预付请求依赖 AutoDL 网页接口；目前通过模拟接口测试和只读元数据、报价核对，尚未实测真实收费克隆或预付订单。订单或支付结果不确定时会停止，须到 AutoDL 订单页面核对，不自动重复支付。

## Windows 直接使用

从本仓库的 Releases 下载 Windows 压缩包，解压后双击 `启动控制台.bat` 或 `AutoDL-Monitor.exe`。首次登录使用自己的 AutoDL 账号，再设置自己的实例 UUID 和抢卡条件。

启动后请保持**最初的程序窗口**打开；关闭这个窗口会停止本地监控并释放端口。只关闭浏览器页面不会停止服务。重复启动同一目录的程序会打开现有页面，仍需通过最初的窗口停止服务。

开机或租用会产生费用，包日/包周/包月会在创建订单后立即支付相应金额。预付订单总额上限按照平台订单金额核验；如果扩容数据盘按日单独计费，日费不包含在该上限中，程序会显示单独报价。未来费用以 AutoDL 规则为准。扩容数据盘按 AutoDL 规则收费，关机后可能继续计费。新租实例不会自动迁移旧实例的数据。停止本地监控不会关闭云端实例或撤销已接受的请求。

## 定时抢卡

勾选“定时开始”，选择本机日期和时间后点击开始。程序在等待期间保持打开，到点按已配置的策略执行；点击“停止抢卡”可以取消等待。关闭电脑、程序或使电脑休眠会中断定时；定时只负责开始，不会自动关闭云端实例。

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
python -m unittest -v test_schedule.py test_billing.py test_clone.py test_monitor.py test_phone_login.py test_share_launch.py
```

测试通过模拟接口检查自动开机、下单防重复、登录流程和启动方式；实际 AutoDL 页面接口与人机验证可能变化，真实短信登录及收费流程需结合平台状态验证。

## 来源与许可

本项目基于 [Jiayi-Fu/autodl-gpu-monitor](https://github.com/Jiayi-Fu/autodl-gpu-monitor)，保留上游提交历史。上游说明其部分代码参考了 [lemonmindyes/autogpu](https://github.com/lemonmindyes/autogpu)。上游仓库目前未声明开源许可证；本仓库未擅自添加许可证。使用或再次分发上游代码前，请自行确认相关授权。
