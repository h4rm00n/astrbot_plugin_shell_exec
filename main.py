import asyncio
import os
import shlex
from typing import Optional

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult, EventResultType
from astrbot.api.platform import MessageType
from astrbot.core.message.components import Plain
from astrbot.core.star import Star
from astrbot.api.star import Context, register
from astrbot.api import AstrBotConfig
from astrbot.api.event import filter
from astrbot.core.star.filter.permission import PermissionType


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
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def execute_shell_command(self, event: AstrMessageEvent, command: Optional[str] = None) -> str:
        """
        执行 shell 命令的 LLM 工具
        
        Args:
            command(string): 要执行的 shell 命令
        """
        if command is None:
            logger.warning("execute_shell_command 在没有 'command' 参数的情况下被调用。")
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