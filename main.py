import asyncio
import os
import shlex
from typing import Optional

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult, EventResultType
from astrbot.api.platform import MessageType
from astrbot.core.message.components import Plain
from astrbot.core.star import Star
from astrbot.core.star.register import register_command, register_permission_type, register_llm_tool
from astrbot.core.star.filter.permission import PermissionType


class ShellExec(Star):
    """Shell 执行插件，提供命令执行功能给用户和 LLM"""
    
    def __init__(self, context, config: dict | None = None):
        super().__init__(context, config)
        # 可以在这里添加配置，比如允许的命令白名单
        self.allowed_commands = config.get("allowed_commands", []) if config else []
        self.allow_all_commands = config.get("allow_all_commands", False) if config else False
    
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
            args = shlex.split(command)
            
            # 安全检查：如果配置了命令白名单且不允许所有命令
            if not self.allow_all_commands and self.allowed_commands:
                # 检查命令是否在白名单中
                if args[0] not in self.allowed_commands:
                    return "", f"错误: 命令 '{args[0]}' 不在允许的命令列表中", 1
            
            # 执行命令
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            # 解码输出
            stdout_text = stdout.decode('utf-8', errors='replace').strip()
            stderr_text = stderr.decode('utf-8', errors='replace').strip()
            
            return stdout_text, stderr_text, process.returncode or 0
            
        except Exception as e:
            logger.error(f"执行命令时出错: {str(e)}")
            return "", f"执行命令时出错: {str(e)}", 1
    
    @register_command("shell")
    @register_permission_type(PermissionType.ADMIN)
    async def shell_command(self, event: AstrMessageEvent, command: str = "") -> MessageEventResult:
        """
        执行 shell 命令的用户命令
        
        Args:
            event: 消息事件
            command: 要执行的 shell 命令
            
        Returns:
            MessageEventResult: 命令执行结果
        """
        if not command:
            return MessageEventResult(
                chain=[Plain("请提供要执行的命令。使用方法: /shell <命令>")],
                result_type=EventResultType.CONTINUE
            )
        
        logger.info(f"管理员 {event.get_sender_id()} 请求执行命令: {command}")
        
        stdout, stderr, return_code = await self._execute_command(command)
        
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
        
        return MessageEventResult(
            chain=[Plain(response)],
            result_type=EventResultType.CONTINUE
        )
    
    @register_llm_tool("execute_shell_command")
    @register_permission_type(PermissionType.ADMIN)
    async def execute_shell_command(self, event: AstrMessageEvent, command: str) -> str:
        """
        执行 shell 命令的 LLM 工具
        
        Args:
            event: 消息事件
            command: 要执行的 shell 命令
            
        Returns:
            str: 命令执行结果，如果出错则返回错误信息
        """
        logger.info(f"LLM 请求执行命令: {command}")
        
        stdout, stderr, return_code = await self._execute_command(command)
        
        # 构建响应
        if stdout and not stderr:
            return f"命令执行成功，返回码: {return_code}\n输出:\n{stdout}"
        elif stderr:
            return f"命令执行失败，返回码: {return_code}\n错误信息:\n{stderr}"
        else:
            return f"命令执行完成，返回码: {return_code}，没有输出。"