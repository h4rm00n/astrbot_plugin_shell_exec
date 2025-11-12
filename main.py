import asyncio
import os
import shlex
from typing import Optional

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult, EventResultType
from astrbot.api.platform import MessageType
from astrbot.core.message.components import File, Plain
from astrbot.core.star import Star
from astrbot.api.star import Context, register
from astrbot.api import AstrBotConfig
from astrbot.api.event import filter


@register("shell_exec", "AstrBot", "Shell 命令执行插件", "1.0.0", "https://github.com/AstrBotDevs/astrbot_plugin_shell_exec")
class ShellExec(Star):
    """Shell 执行插件，提供命令执行功能给用户和 LLM"""
    
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 从配置中获取设置
        self.allowed_commands = config.get("allowed_commands", [])
        self.allow_all_commands = config.get("allow_all_commands", False)
        self.max_execution_time = config.get("max_execution_time", 30)
        self.enable_logging = config.get("enable_logging", True)
        self.interactive_commands = config.get("interactive_commands", [])
    
    async def _execute_command(self, command: str) -> tuple[str, str, int]:
        """
        执行 shell 命令的核心方法
        
        Args:
            command: 要执行的 shell 命令
            
        Returns:
            tuple: (stdout, stderr, return_code)
        """
        try:
            # 使用 shlex.split 来正确处理带引号的参数
            # 使用 shlex.split 来正确处理带引号的参数，并展开用户目录（~）
            args = [os.path.expanduser(arg) for arg in shlex.split(command)]
            
            # 交互式命令检查
            if args[0] in self.interactive_commands and len(args) == 1:
                return "", f"错误: 直接执行 '{args[0]}' 会启动一个交互式会话并导致堵塞。请提供参数（例如，'{args[0]} -c \"...\"'）或要执行的脚本。", 1

            # 安全检查：如果配置了命令白名单且不允许所有命令
            if not self.allow_all_commands and self.allowed_commands:
                # 检查命令是否在白名单中
                if args[0] not in self.allowed_commands:
                    return "", f"错误: 命令 '{args[0]}' 不在允许的命令列表中", 1
            
            # 记录日志（如果启用）
            if self.enable_logging:
                logger.info(f"执行命令: {command}")
            
            # 执行命令
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # 添加超时处理
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.max_execution_time
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return "", f"命令执行超时（超过 {self.max_execution_time} 秒）", 1
            
            # 解码输出
            stdout_text = stdout.decode('utf-8', errors='replace').strip()
            stderr_text = stderr.decode('utf-8', errors='replace').strip()
            
            return stdout_text, stderr_text, process.returncode or 0
            
        except Exception as e:
            logger.error(f"执行命令时出错: {str(e)}")
            return "", f"执行命令时出错: {str(e)}", 1
    
    @filter.command("shell")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def shell_command(self, event: AstrMessageEvent, command: str = ""):
        """
        执行 shell 命令的用户命令
        
        Args:
            event: 消息事件
            command: 要执行的 shell 命令 (由框架注入，可能不完整)
        """
        # 框架注入的 command 参数不可靠，我们从原始消息中手动解析
        message_text = event.message_str.strip()
        
        # 找到 /shell 之后的所有内容
        parts = message_text.split(" ", 1)
        if len(parts) > 1:
            actual_command = parts[1].strip()
        else:
            actual_command = ""

        if not actual_command:
            yield event.plain_result("请提供要执行的命令。使用方法: /shell <命令>")
            return
        
        logger.info(f"管理员 {event.get_sender_id()} 请求执行命令: {actual_command}")
        
        stdout, stderr, return_code = await self._execute_command(actual_command)
        
        # 构建响应消息
        response_parts = []
        
        if stdout:
            response_parts.append(f"输出:\n```\n{stdout}\n```")
        
        if stderr:
            response_parts.append(f"错误:\n```\n{stderr}\n```")
        
        if not stdout and not stderr:
            response_parts.append("命令执行完成，没有输出。")
        
        response_parts.append(f"返回码: {return_code}")
        
        response = "\n\n".join(response_parts)
        
        yield event.plain_result(response)
    
    @filter.llm_tool(name="execute_shell_command")
    async def execute_shell_command(self, event: AstrMessageEvent, command: Optional[str] = None) -> str:
        """
        执行 shell 命令的 LLM 工具
        
        Args:
            command(string): 要执行的 shell 命令
        """
        # 权限检查：只有管理员才能通过 LLM 执行 shell 命令
        if event.role != "admin":
            logger.warning(f"权限不足：用户 {event.get_sender_id()} (角色: {event.role}) 尝试通过 LLM 执行 shell 命令。")
            return "权限验证失败：用户不是管理员，无权限使用shell命令。请联系管理员获取权限。操作已终止，无需重复尝试。"

        # 检查是否为框架对用户命令（如 /shell）的误调用
        if event.message_str.strip().startswith("/"):
            logger.debug(f"忽略框架对 LLM 工具的误调用，原始消息: {event.message_str.strip()}")
            return ""
        
        if command is None:
            logger.warning("LLM 工具 'execute_shell_command' 被调用，但缺少必需的 'command' 参数。")
            return ""
            
        logger.info(f"LLM 请求执行命令: {command}")
        
        stdout, stderr, return_code = await self._execute_command(command)
        
        # 构建响应
        if stdout and not stderr:
            return f"命令执行成功，返回码: {return_code}\n输出:\n{stdout}"
        elif stderr:
            return f"命令执行失败，返回码: {return_code}\n错误信息:\n{stderr}"
        else:
            return f"命令执行完成，返回码: {return_code}，没有输出。"

    @filter.command("send_file")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def send_file_command(self, event: AstrMessageEvent, path: str = ""):
        """
        根据路径发送文件的用户命令
        
        Args:
            event: 消息事件
            path: 要发送的文件路径 (由框架注入，可能不完整)
        """
        message_text = event.message_str.strip()
        
        parts = message_text.split(" ", 1)
        if len(parts) > 1:
            actual_path = parts[1].strip()
        else:
            actual_path = ""

        if not actual_path:
            yield event.plain_result("请提供要发送的文件路径。使用方法: /send_file <路径>")
            return
        
        expanded_path = os.path.expanduser(actual_path)

        if not os.path.exists(expanded_path):
            yield event.plain_result(f"文件未找到: {expanded_path}")
            return
        
        if not os.path.isfile(expanded_path):
            yield event.plain_result(f"路径不是一个文件: {expanded_path}")
            return

        logger.info(f"管理员 {event.get_sender_id()} 请求发送文件: {expanded_path}")
        
        try:
            file_component = File(name=os.path.basename(expanded_path), file=expanded_path)
            yield event.chain_result([file_component])
        except Exception as e:
            logger.error(f"发送文件时出错: {e}")
            yield event.plain_result(f"发送文件时出错: {e}")

    @filter.llm_tool(name="send_file_by_path")
    async def send_file_by_path(self, event: AstrMessageEvent, path: Optional[str] = None) -> str:
        """
        根据路径发送文件的 LLM 工具
        
        Args:
            path(string): 要发送的文件的绝对或相对路径
        """
        # 权限检查
        if event.role != "admin":
            logger.warning(f"权限不足：用户 {event.get_sender_id()} (角色: {event.role}) 尝试通过 LLM 发送文件。")
            return "权限验证失败：用户不是管理员，无权限发送文件。请联系管理员获取权限。操作已终止，无需重复尝试。"

        if path is None:
            logger.warning("LLM 工具 'send_file_by_path' 被调用，但缺少必需的 'path' 参数。")
            return "参数错误: 'path' 参数是必需的。"
            
        logger.info(f"LLM 请求发送文件: {path}")
        
        expanded_path = os.path.expanduser(path)

        if not os.path.exists(expanded_path):
            return f"文件未找到: {expanded_path}"
        
        if not os.path.isfile(expanded_path):
            return f"路径不是一个文件: {expanded_path}"

        try:
            file_component = File(name=os.path.basename(expanded_path), file=expanded_path)
            # LLM tool needs to send the message itself.
            await event.send(MessageChain([file_component]))
            return f"文件 '{os.path.basename(expanded_path)}' 已成功发送。"
        except Exception as e:
            logger.error(f"LLM 工具发送文件时出错: {e}")
            return f"发送文件时出错: {e}"