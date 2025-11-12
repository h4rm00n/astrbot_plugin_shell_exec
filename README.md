# AstrBot Shell 执行插件

一个为 AstrBot 设计的插件，允许管理员执行 shell 命令，并提供给大模型调用的工具接口。

## 功能特性

- 🔒 **安全控制**: 仅允许管理员使用，支持命令白名单
- 💬 **用户命令**: 通过 `/shell` 命令直接执行 shell 命令
- 🤖 **LLM 工具**: 提供给大模型调用的 `execute_shell_command` 工具
- ⚙️ **可配置**: 支持通过配置文件自定义允许的命令

## 安装方法

1. 将插件文件夹放置在 AstrBot 的 `plugins` 目录下
2. 重启 AstrBot 或使用插件重载命令

## 配置说明

在插件目录下创建 `config.yaml` 文件（可参考 `config_example.yaml`）：

```yaml
# 是否允许执行所有命令（默认为 false，建议保持 false 以确保安全）
allow_all_commands: false

# 允许执行的命令白名单（仅在 allow_all_commands 为 false 时生效）
allowed_commands:
  - "ls"
  - "pwd"
  - "whoami"
  - "date"
  - "uptime"
  - "df"
  - "free"
  - "ps"
  - "cat"
  - "grep"
  - "tail"
  - "head"
  - "wc"
  - "find"
  - "echo"
  - "uname"
  - "which"
  - "whereis"
```

## 使用方法

### 用户命令

管理员可以使用 `/shell` 命令执行 shell 命令：

```
/shell ls -la
/shell pwd
/shell whoami
```

### LLM 工具

大模型可以调用 `execute_shell_command` 工具来执行命令，例如：

```
请帮我查看当前目录的文件列表
```

大模型会自动调用工具执行 `ls` 命令并返回结果。

## 安全注意事项

⚠️ **重要安全提示**：

1. **仅限管理员**: 只有被 AstrBot 识别为管理员用户才能使用此插件
2. **命令白名单**: 建议始终使用命令白名单，不要启用 `allow_all_commands`
3. **谨慎添加命令**: 在白名单中添加命令时，请确保这些命令不会造成安全风险
4. **避免危险命令**: 不要将以下命令添加到白名单中：
   - `rm` - 删除文件
   - `sudo` - 获取管理员权限
   - `su` - 切换用户
   - `chmod` - 修改文件权限
   - `chown` - 修改文件所有者
   - `dd` - 磁盘操作
   - `mkfs` - 格式化文件系统
   - `reboot` / `shutdown` - 重启/关机
   - `passwd` - 修改密码

## 权限说明

插件使用 AstrBot 的权限系统，只有被识别为管理员的用户才能使用此插件的功能。如果非管理员尝试使用，将会收到权限不足的提示。

## 故障排除

### 命令执行失败

1. 检查命令是否在白名单中（如果未启用 `allow_all_commands`）
2. 确认用户是否具有管理员权限
3. 查看 AstrBot 日志获取详细错误信息

### 配置不生效

1. 确认配置文件名为 `config.yaml`
2. 检查 YAML 格式是否正确
3. 重启 AstrBot 或重载插件

## 开发信息

- 插件名称: `astrbot_plugin_shell_exec`
- 版本: v0.1.0
- 作者: Harmoon
- 仓库: https://github.com/h4rm00n/astrbot_plugin_shell_exec

## 许可证

本插件遵循 MIT 许可证。
