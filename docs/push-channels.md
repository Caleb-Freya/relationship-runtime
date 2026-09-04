# 推送通道从零指南：Bark 与 ntfy

本项目不自己发短信、不自己做 App——想找你的时候，靠 Bark 或 ntfy 这两个
**第三方免费开源推送工具**把消息送到你手机上。它们跟本项目没有从属关系，
你可以把它们理解成"帮你把一条消息送进手机通知栏"的快递员。

如果你从没听说过这两个名字，这篇文档从零讲清楚：是什么、怎么装、怎么填进
`config.yaml`。

## Bark 是什么（iOS）

Bark 是一个 iOS 上的免费开源推送 App，只做一件事：**接收别人发给你的推送
通知**。

1. 打开 App Store，搜索 "Bark"，安装。
2. 打开 App，你会看到一条形如 `https://api.day.app/YOUR_KEY_HERE` 的地址——
   这就是你专属的推送地址。`YOUR_KEY_HERE` 是 App 自动生成的一串随机字符
   （不是你自己设的密码，也不用记，App 里随时能看）。
3. 别人（也就是运行本项目的这个进程）只要往这条地址发一个请求，你的手机
   就会收到通知。

消息默认经 Bark 官方服务器（`api.day.app`）中转。如果不想让消息经过第三方
服务器，Bark 也支持完全自建，官方仓库见
[github.com/Finb/Bark](https://github.com/Finb/Bark)，里面有自建服务器的
说明。

### 填进 config.yaml

对应本项目核心调度器（`providers.notification`）：

```yaml
providers:
  notification: bark
  bark:
    url: https://api.day.app/YOUR_KEY_HERE   # 换成你在 App 里看到的那条地址
```

如果你还接了情绪哨兵（`adapters/sentinel`，可选组件），它用的是另一组键，
**只填 key 这一段**，不是完整地址：

```yaml
sentinel:
  push_provider: bark
  bark_key: "YOUR_KEY_HERE"   # 只填 key，或用环境变量 RR_BARK_KEY 代替写进文件
```

## ntfy 是什么（安卓 / 桌面 / 全平台）

ntfy 是一个开源免费的推送服务，安卓、iOS、桌面浏览器都能用，尤其是
**安卓用户的首选**（Bark 是 iOS 独占，安卓装不了）。

1. 安卓用户：Google Play 或 F-Droid 商店搜索 "ntfy"，安装。
2. ntfy 不用账号密码，靠的是**订阅一个 topic**——topic 本质是一个你自己
   起的暗号（一串字符串）。谁知道这个 topic 名字，谁就能往这个 topic 发消息
   / 收到推送，所以**起名要够长够随机**，别用 "wo-de-tuisong" 这种一猜就中
   的名字。
3. 打开 App，点订阅，输入你起的 topic 名字即可。

默认走 ntfy 官方公共服务器 [ntfy.sh](https://ntfy.sh)，免费直接能用。也支持
完全自建（Docker 一行命令），自建说明见官方文档
[docs.ntfy.sh](https://docs.ntfy.sh)。

### 填进 config.yaml

对应本项目核心调度器（`providers.notification`）：

```yaml
providers:
  notification: ntfy
  ntfy:
    url: https://ntfy.sh              # 公共服务器地址；自建的话换成你自己的
    topic: "your-long-random-topic"   # 换成你起的 topic，务必够长够随机
    token: ""                         # 自建且开了鉴权才需要填
```

对应情绪哨兵（`adapters/sentinel`，可选组件）：

```yaml
sentinel:
  push_provider: ntfy
  ntfy:
    url: https://ntfy.sh
    topic: "your-long-random-topic"   # 或用环境变量 RR_NTFY_TOPIC
    token: ""
```

## 怎么选

- 用 **iPhone** → 选 Bark。装个 App、打开就能看到地址，不用自己起名字。
- 用 **安卓** → 选 ntfy。Bark 是 iOS 独占，安卓用不了。

## 隐私提醒

不管选哪个，推送内容都会经过第三方服务器中转——Bark 默认经
`api.day.app`，ntfy 默认经 `ntfy.sh`。这意味着理论上运营方的服务器能看到
经过它的推送内容（本项目自身有 `privacy.store_message_text` 等配置控制
推送正文写多细，但一旦发出去，链路上的服务器就是第三方的）。

如果介意，两边都支持自建，自己的服务器只有自己能看：

- Bark 自建：见 [github.com/Finb/Bark](https://github.com/Finb/Bark) 仓库
  说明。
- ntfy 自建：见 [docs.ntfy.sh](https://docs.ntfy.sh) 的自托管
  （self-hosting）文档。
