# AstrBot Shell 执行插件

一个为 AstrBot 设计的插件，允许管理员执行 shell 命令，并提供给大模型调用的工具接口。

## 功能特性

- 🚀 **完整的 Shell 支持**: 直接在机器人中执行复杂的 shell 命令，支持管道 (`|`)、重定向 (`>`) 和命令链 (`;`, `&&`)。
- 🔒 **管理员权限**: 所有命令执行功能都严格限制为 AstrBot 的管理员用户。
- 💬 **用户命令**: 通过 `/shell` 命令直接执行任意 shell 命令。
- 🤖 **LLM 工具**: 提供给大模型调用的 `execute_shell_command` 工具，使其具备执行系统命令的能力。
- 📁 **文件发送**: 附带 `/send_file` 命令和 `send_file_by_path` LLM 工具，方便地从服务器发送文件。

## 安装方法

1.  将插件文件夹放置在 AstrBot 的 `plugins` 目录下。
2.  重启 AstrBot 或使用插件重载命令。

## 配置说明

此插件的旧版本包含 `allowed_commands` 等配置项。在最新的版本中，我们采纳了新的安全模型，**移除了所有命令白名单相关的配置**。

插件的安全性现在完全由 AstrBot 的管理员权限系统来保证。您可以配置的选项仅剩：

在插件目录下创建 `config.yaml` 文件（可参考 `config_example.yaml`）：

```yaml
# 命令执行的超时时间（秒），防止命令长时间无响应。默认为 30。
max_execution_time: 30

# 是否在 AstrBot 日志中记录所有被执行的命令。默认为 true。
enable_logging: true
```

## 使用方法

### 用户命令

管理员可以使用 `/shell` 和 `/send_file` 命令：

```
# 执行简单命令
/shell ls -la

# 使用管道和重定向
/shell echo "Hello World" > /tmp/hello.txt

# 发送文件
/send_file /tmp/hello.txt
```

### LLM 工具

大模型可以调用 `execute_shell_command` 和 `send_file_by_path` 工具来执行操作。

## ⚠️ 重要安全注意事项 ⚠️

本插件的设计理念是**完全信任拥有管理员权限的用户**。

这意味着，一旦一个用户被设为 AstrBot 的管理员，他们就通过本插件获得了在服务器上**以 AstrBot 进程所用账户执行任意 shell 命令**的权限。这等同于给了他们一个服务器的 shell 访问权限。

**因此，安全责任完全在于服务器的拥有者：**

1.  **谨慎授予管理员权限**: **绝对不要**将不完全受信任的人设置为 AstrBot 的管理员。
2.  **管理员应自我约束**: 作为管理员，您必须清楚自己正在服务器上执行命令。请勿执行任何您不完全理解或可能产生危险后果的命令，例如 `rm -rf /`、`mkfs` 或任何可能破坏系统、泄露数据的操作。

本插件不提供应用内的安全护栏（如命令黑名单），因为它假定管理员是受信任的、有能力且负责任的操作者。

## 故障排除

### 命令执行失败

1.  确认您是否具有 AstrBot 的管理员权限。
2.  检查命令语法是否正确。
3.  查看 AstrBot 日志获取详细错误信息。

## 开发信息

-   插件名称: `astrbot_plugin_shell_exec`
-   版本: v1.0.0
-   作者: Harmoon
-   仓库: https://github.com/h4rm00n/astrbot_plugin_shell_exec

## 许可证

本插件遵循 MIT 许可证。
