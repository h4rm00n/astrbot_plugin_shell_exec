import asyncio
import os
import shlex
from typing import Optional
import aiohttp
import uuid
import tempfile

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult, EventResultType
from astrbot.api.platform import MessageType
from astrbot.api.message_components import File, Plain
from astrbot.api.star import Star
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
        self.max_execution_time = config.get("max_execution_time", 30)
        self.enable_logging = config.get("enable_logging", True)
        
        # 设置工作目录
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        default_cwd = os.path.join(plugin_dir, "workdir")
        self.working_directory = config.get("working_directory") or default_cwd
        
        # 确保工作目录存在
        os.makedirs(self.working_directory, exist_ok=True)
    
    async def _execute_command(self, command: str) -> tuple[str, str, int]:
        """
        执行 shell 命令的核心方法
        
        Args:
            command: 要执行的 shell 命令
            
        Returns:
            tuple: (stdout, stderr, return_code)
        """
        try:
            # 安全警告: 为了支持管道(|)和重定向(>)等shell特性，我们使用`create_subprocess_shell`。
            # 这意味着命令将由系统的shell（如/bin/sh）直接解释。
            # 虽然这提供了强大的功能，但也带来了安全风险，因为可以链式执行命令（例如 `cmd1; cmd2`）。
            # 因此，插件的安全性现在完全依赖于调用者的权限检查（例如，仅限管理员）。
            # 原有的`allowed_commands`白名单机制在shell模式下几乎无效，因此已被移除。

            # 记录日志（如果启用）
            if self.enable_logging:
                logger.info(f"在shell中执行命令: {command}")

            # 使用 shell 执行命令
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=self.working_directory,
                stdin=asyncio.subprocess.DEVNULL,
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
        response = ""
        if stdout and not stderr:
            response = f"命令执行成功，返回码: {return_code}\n输出:\n{stdout}"
        elif stderr:
            response = f"命令执行失败，返回码: {return_code}\n错误信息:\n{stderr}"
        else:
            response = f"命令执行完成，返回码: {return_code}，没有输出。"

        # 将工具执行结果直接发送给用户，提供即时反馈
        feedback_message = (
            f"LLM 工具 'execute_shell_command' 执行了命令: `{command}`\n\n"
            f"执行结果：\n{response}"
        )
        await event.send(MessageChain([Plain(feedback_message)]))

        # 将结果返回给 LLM
        return response

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
        根据本地路径发送文件的 LLM 工具
        
        Args:
            path(string): 要发送的文件的绝对或相对本地路径
        """
        # 权限检查
        if event.role != "admin":
            logger.warning(f"权限不足：用户 {event.get_sender_id()} (角色: {event.role}) 尝试通过 LLM 发送文件。")
            return "权限验证失败：用户不是管理员，无权限发送文件。请联系管理员获取权限。操作已终止，无需重复尝试。"

        feedback_prefix = f"LLM 工具 'send_file_by_path' 尝试发送文件: `{path}`\n\n"

        if path is None:
            response = "参数错误: 'path' 参数是必需的。"
            logger.warning("LLM 工具 'send_file_by_path' 被调用，但缺少必需的 'path' 参数。")
            await event.send(MessageChain([Plain(f"LLM 工具 'send_file_by_path' 被调用，但缺少必需的 'path' 参数。\n\n执行结果：\n{response}")]))
            return response

        expanded_path = os.path.expanduser(path)
        if not os.path.exists(expanded_path):
            response = f"文件未找到: {expanded_path}"
            await event.send(MessageChain([Plain(f"{feedback_prefix}执行结果：\n{response}")]))
            return response
        
        if not os.path.isfile(expanded_path):
            response = f"路径不是一个文件: {expanded_path}"
            await event.send(MessageChain([Plain(f"{feedback_prefix}执行结果：\n{response}")]))
            return response

        try:
            logger.info(f"LLM 请求发送文件: {expanded_path}")
            file_component = File(name=os.path.basename(expanded_path), file=expanded_path)
            await event.send(MessageChain([file_component]))
            
            response = f"文件 '{os.path.basename(expanded_path)}' 已成功发送。"
            # 只需要返回成功信息即可，因为文件已经发送了
            return response
        except Exception as e:
            response = f"发送文件时出错: {e}"
            logger.error(f"LLM 工具发送文件时出错: {e}")
            await event.send(MessageChain([Plain(f"{feedback_prefix}执行结果：\n{response}")]))
            return response

    @filter.llm_tool(name="send_file_by_url")
    async def send_file_by_url(self, event: AstrMessageEvent, url: Optional[str] = None) -> str:
        """
        根据 URL 发送文件的 LLM 工具，例如在线图片。

        Args:
            url(string): 要发送的文件的 URL
        """
        # 权限检查
        if event.role != "admin":
            logger.warning(f"权限不足：用户 {event.get_sender_id()} (角色: {event.role}) 尝试通过 LLM 发送文件。")
            return "权限验证失败：用户不是管理员，无权限发送文件。请联系管理员获取权限。操作已终止，无需重复尝试。"

        feedback_prefix = f"LLM 工具 'send_file_by_url' 尝试发送文件: `{url}`\n\n"

        if url is None:
            response = "参数错误: 'url' 参数是必需的。"
            logger.warning("LLM 工具 'send_file_by_url' 被调用，但缺少必需的 'url' 参数。")
            await event.send(MessageChain([Plain(f"LLM 工具 'send_file_by_url' 被调用，但缺少必需的 'url' 参数。\n\n执行结果：\n{response}")]))
            return response

        temp_dir = self.working_directory
        
        # 从 URL 中提取文件名，如果无法提取则生成一个
        try:
            file_name = os.path.basename(url.split("?")[0])
            if not file_name:
                file_name = str(uuid.uuid4())
        except Exception:
            file_name = str(uuid.uuid4())

        temp_file_path = os.path.join(temp_dir, file_name)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        response = f"下载文件失败，HTTP 状态码: {resp.status}"
                        await event.send(MessageChain([Plain(f"{feedback_prefix}执行结果：\n{response}")]))
                        return response
                    
                    with open(temp_file_path, 'wb') as f:
                        while True:
                            chunk = await resp.content.read(1024)
                            if not chunk:
                                break
                            f.write(chunk)

            logger.info(f"LLM 请求发送 URL 文件: {url}")
            file_component = File(name=file_name, file=temp_file_path)
            await event.send(MessageChain([file_component]))
            
            response = f"文件 '{file_name}' 已从 URL 成功发送。"
            return response
        except Exception as e:
            response = f"从 URL 发送文件时出错: {e}"
            logger.error(f"LLM 工具从 URL 发送文件时出错: {e}")
            await event.send(MessageChain([Plain(f"{feedback_prefix}执行结果：\n{response}")]))
            return response
        finally:
            # 可选：发送后删除临时文件
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception as e:
                    logger.warning(f"删除临时文件失败: {temp_file_path}, 错误: {e}")