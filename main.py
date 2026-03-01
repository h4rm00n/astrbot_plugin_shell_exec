import asyncio
import os
import shlex
import re
import time
import json
from typing import Optional, Tuple, Dict, List
from dataclasses import dataclass, field
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


class ToolChainInterrupt(BaseException):
    """
    自定义异常，用于中断工具链执行。
    继承自 BaseException 而非 Exception，以穿透框架的 except Exception 捕获。
    """
    def __init__(self, message: str, pending_tools: List[str] = None):
        super().__init__(message)
        self.message = message
        self.pending_tools = pending_tools or []


@dataclass
class PendingCommand:
    command: str
    timestamp: float
    reason: str
    source: str  # 'user' or 'llm'
    umo: str = ""  # 记录原始会话 ID
    pending_tool_calls: List[str] = field(default_factory=list)  # 记录被阻塞时尚未执行的工具调用

# @register("shell_exec", "AstrBot", "Shell 命令执行插件", "1.1.0", "https://github.com/h4rm00n/astrbot_plugin_shell_exec")
class ShellExec(Star):
    """Shell 执行插件，提供命令执行功能给用户和 LLM，具备三级安全审计和确认状态机"""
    
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 从配置中获取设置
        self.max_execution_time = config.get("max_execution_time", 30)
        self.enable_logging = config.get("enable_logging", True)
        
        # 安全等级：用户指令 vs LLM 指令
        self.user_security_level = config.get("user_security_level", "permissive")
        self.llm_security_level = config.get("llm_security_level", "verification")
        
        self.security_blacklist = config.get("security_blacklist", ["rm", "mkfs", "format", "shutdown", "reboot", "chmod 777", "> /dev", "mv /*"])
        self.enable_llm_audit = config.get("enable_llm_audit", True)
        
        # 待确认命令缓存 {user_id: PendingCommand}
        self.pending_states: Dict[str, PendingCommand] = {}
        # 确认有效期（秒），超时后自动失效
        self.confirmation_timeout = 300 
        
        # 设置工作目录
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        default_cwd = os.path.join(plugin_dir, "workdir")
        self.working_directory = config.get("working_directory") or default_cwd
        
        # 确保工作目录存在
        os.makedirs(self.working_directory, exist_ok=True)

    async def _check_security(self, command: str, current_level: str, umo: Optional[str] = None) -> Tuple[bool, str]:
        """
        检查命令安全性
        Returns: (is_safe, reason)
        """
        # 1. 本地黑名单检查
        for word in self.security_blacklist:
            if word in command:
                return False, f"命令包含黑名单词汇: {word}"

        # 2. LLM 语义审计
        if self.enable_llm_audit:
            try:
                prompt = (
                    "作为一名系统安全专家，请评估以下 Shell 命令的安全性。\n"
                    f"命令: `{command}`\n\n"
                    "要求：\n"
                    "1. 如果该命令可能导致系统崩溃、关键数据丢失、敏感信息泄露（如读取 /etc/passwd）或提权，请判定为 UNSAFE。\n"
                    "2. 如果命令是常规的查询、文件操作或无害的系统管理，请判定为 SAFE。\n"
                    "3. 仅返回 SAFE 或 UNSAFE，不要有任何额外文字。"
                )
                
                # 获取当前使用的提供商
                provider = self.context.get_using_provider(umo)
                response = await provider.text_chat(prompt=prompt)
                
                audit_result = response.completion_text.strip().upper()
                if "UNSAFE" in audit_result:
                    return False, "LLM 语义审计判定该命令具有潜在风险。"
                
            except Exception as e:
                logger.error(f"LLM 安全审计出错: {e}")
                # 审计出错时，如果是严格模式，则保守处理
                if current_level == "strict":
                    return False, f"安全审计异常且处于严格模式: {e}"

        return True, ""

    async def _execute_command(self, command: str) -> tuple[str, str, int]:
        """执行 shell 命令的核心方法"""
        try:
            if self.enable_logging:
                logger.info(f"在shell中执行命令: {command}")

            process = await asyncio.create_subprocess_shell(
                command,
                cwd=self.working_directory,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.max_execution_time
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return "", f"命令执行超时（超过 {self.max_execution_time} 秒）", 1
            
            stdout_text = stdout.decode('utf-8', errors='replace').strip()
            stderr_text = stderr.decode('utf-8', errors='replace').strip()
            
            return stdout_text, stderr_text, process.returncode or 0
            
        except Exception as e:
            logger.error(f"执行命令时出错: {str(e)}")
            return "", f"执行命令时出错: {str(e)}", 1
    
    @filter.command("shell")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def shell_command(self, event: AstrMessageEvent, command: str = ""):
        """执行 shell 命令的用户命令"""
        message_text = event.message_str.strip()
        parts = message_text.split(" ", 1)
        actual_command = parts[1].strip() if len(parts) > 1 else ""

        if not actual_command:
            yield event.plain_result("请提供要执行的命令。使用方法: /shell <命令>")
            return

        user_id = event.get_sender_id()
        
        # 状态检查：如果当前用户已有待确认命令，提示先处理
        if user_id in self.pending_states:
            pending = self.pending_states[user_id]
            if time.time() - pending.timestamp < self.confirmation_timeout:
                yield event.plain_result(
                    f"⚠️ 您当前有一个待确认的高危命令（来自 {pending.source}）：\n`{pending.command}`\n\n"
                    "请先使用 `/shell_allow` 确认执行，或使用 `/shell_deny` 取消。"
                )
                return
            else:
                del self.pending_states[user_id]

        # --- 安全校验逻辑 (用户级) ---
        if self.user_security_level != "permissive":
            is_safe, reason = await self._check_security(actual_command, self.user_security_level, event.unified_msg_origin)
            if not is_safe:
                if self.user_security_level == "strict":
                    yield event.plain_result(f"🚫 命令已被拦截！\n原因: {reason}")
                    return
                elif self.user_security_level == "verification":
                    self.pending_states[user_id] = PendingCommand(
                        command=actual_command,
                        timestamp=time.time(),
                        reason=reason,
                        source='user'
                    )
                    yield event.plain_result(
                        f"⚠️ 风险预警：该指令可能存在风险！\n原因: {reason}\n\n"
                        f"待执行指令: `{actual_command}`\n\n"
                        "若您确定要执行，请输入 `/shell_allow` 进行确认。"
                    )
                    return

        # 执行通过审计的命令
        async for res in self._run_and_yield_result(event, actual_command):
            yield res

    @filter.command("shell_allow")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def shell_allow_command(self, event: AstrMessageEvent):
        """确认并执行之前被拦截的高危命令"""
        user_id = event.get_sender_id()
        if user_id not in self.pending_states:
            yield event.plain_result("❌ 当前没有需要确认的命令。")
            return
        
        pending = self.pending_states.pop(user_id)
        
        # 超时检查
        if time.time() - pending.timestamp > self.confirmation_timeout:
            yield event.plain_result("⏰ 确认已超时，请重新发起命令。")
            return
        
        logger.info(f"管理员 {user_id} 确认执行由 {pending.source} 发起的命令: {pending.command}")
        yield event.plain_result(f"✅ 已确认，正在执行: `{pending.command}`")
        
        stdout, stderr, return_code = await self._execute_command(pending.command)
        
        # 构建执行结果文本
        response_parts = []
        if stdout: response_parts.append(f"输出:\n```\n{stdout}\n```")
        if stderr: response_parts.append(f"错误:\n```\n{stderr}\n```")
        if not stdout and not stderr: response_parts.append("命令执行完成，没有输出。")
        response_parts.append(f"返回码: {return_code}")
        result_text = "\n\n".join(response_parts)
        
        yield event.plain_result(result_text)

        # 如果命令源自 LLM，则主动通知 LLM 结果
        if pending.source == 'llm':
            try:
                target_umo = pending.umo if pending.umo else event.unified_msg_origin
                chat_provider_id = await self.context.get_current_chat_provider_id(target_umo)
                
                # 获取原始对话上下文
                history = []
                curr_cid = await self.context.conversation_manager.get_curr_conversation_id(target_umo)
                if curr_cid:
                    conv = await self.context.conversation_manager.get_conversation(target_umo, curr_cid)
                    if conv and conv.history:
                        history = json.loads(conv.history)
                
                notification_prompt = (
                    f"管理员已批准执行你之前请求的敏感命令：`{pending.command}`。\n\n"
                    f"执行结果如下：\n{result_text}\n\n"
                    "请根据此结果继续你之前的推理或任务，并给用户一个回复。"
                )
                llm_response = await self.context.tool_loop_agent(
                    event=event,
                    chat_provider_id=chat_provider_id,
                    prompt=notification_prompt,
                    contexts=history,
                    tools=self.context.get_llm_tool_manager().get_full_tool_set()
                )
                # 将 LLM 的回应发送给用户
                if llm_response and llm_response.completion_text:
                    await event.send(MessageChain([Plain(llm_response.completion_text)]))
                    
                    # 将这次交互写回对话历史，确保后续对话能感知
                    if curr_cid:
                        user_msg = {"role": "user", "content": notification_prompt}
                        assistant_msg = {"role": "assistant", "content": llm_response.completion_text}
                        await self.context.conversation_manager.add_message_pair(
                            cid=curr_cid,
                            user_message=user_msg,
                            assistant_message=assistant_msg
                        )
            except Exception as e:
                logger.error(f"尝试通知 LLM 失败: {e}")

    @filter.command("shell_deny")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def shell_deny_command(self, event: AstrMessageEvent):
        """取消当前待确认的高危命令"""
        user_id = event.get_sender_id()
        if user_id in self.pending_states:
            pending = self.pending_states.pop(user_id)
            yield event.plain_result(f"已取消待执行指令: `{pending.command}`")
            
            # 如果是 LLM 命令，通知 LLM 被拒绝了
            if pending.source == 'llm':
                try:
                    target_umo = pending.umo if pending.umo else event.unified_msg_origin
                    chat_provider_id = await self.context.get_current_chat_provider_id(target_umo)
                    
                    # 获取原始对话上下文
                    history = []
                    curr_cid = await self.context.conversation_manager.get_curr_conversation_id(target_umo)
                    if curr_cid:
                        conv = await self.context.conversation_manager.get_conversation(target_umo, curr_cid)
                        if conv and conv.history:
                            history = json.loads(conv.history)

                    notification_prompt = (
                        f"管理员**拒绝**了你之前请求的敏感命令：`{pending.command}`。\n\n"
                        "请知晓此情况，并向用户解释该操作由于安全策略被管理员拦截。"
                    )
                    llm_response = await self.context.tool_loop_agent(
                        event=event,
                        chat_provider_id=chat_provider_id,
                        prompt=notification_prompt,
                        contexts=history,
                        tools=self.context.get_llm_tool_manager().get_full_tool_set()
                    )
                    if llm_response and llm_response.completion_text:
                        await event.send(MessageChain([Plain(llm_response.completion_text)]))
                        
                        # 将这次交互写回对话历史，确保后续对话能感知
                        if curr_cid:
                            user_msg = {"role": "user", "content": notification_prompt}
                            assistant_msg = {"role": "assistant", "content": llm_response.completion_text}
                            await self.context.conversation_manager.add_message_pair(
                                cid=curr_cid,
                                user_message=user_msg,
                                assistant_message=assistant_msg
                            )
                except Exception as e:
                    logger.error(f"尝试通知 LLM 失败: {e}")
        else:
            yield event.plain_result("当前没有待确认的命令。")

    async def _run_and_yield_result(self, event: AstrMessageEvent, command: str):
        """内部工具：执行命令并 yield 格式化结果"""
        stdout, stderr, return_code = await self._execute_command(command)
        
        response_parts = []
        if stdout: response_parts.append(f"输出:\n```\n{stdout}\n```")
        if stderr: response_parts.append(f"错误:\n```\n{stderr}\n```")
        if not stdout and not stderr: response_parts.append("命令执行完成，没有输出。")
        response_parts.append(f"返回码: {return_code}")
        
        yield event.plain_result("\n\n".join(response_parts))

    @filter.llm_tool(name="execute_shell_command")
    async def execute_shell_command(self, event: AstrMessageEvent, command: Optional[str] = None) -> Optional[str]:
        """
        执行 shell 命令的 LLM 工具。该工具仅限管理员通过 LLM 调用。
        
        Args:
            command(string): 要执行的 shell 命令
        """
        if event.role != "admin":
            return "权限验证失败：用户不是管理员。"
        
        if not command: return "错误：缺少 command 参数。"

        user_id = event.get_sender_id()

        # --- 安全校验逻辑 (LLM级) ---
        if self.llm_security_level != "permissive":
            is_safe, reason = await self._check_security(command, self.llm_security_level, event.unified_msg_origin)
            if not is_safe:
                if self.llm_security_level == "strict":
                    logger.warning(f"LLM 危险指令被硬拦截: {command}, 原因: {reason}")
                    # 发送消息给用户
                    await event.send(MessageChain([Plain(
                        f"🛡️ 安全审计拦截了 LLM 生成的指令: `{command}`\n原因: {reason}\n\n"
                        "🚫 工具链已强制终止，后续操作不会执行。"
                    )]))
                    # 抛出 BaseException 子类来穿透框架的 except Exception 捕获，中断工具循环
                    raise ToolChainInterrupt(
                        f"命令被安全策略拦截: {reason}",
                        pending_tools=[]
                    )
                
                elif self.llm_security_level == "verification":
                    self.pending_states[user_id] = PendingCommand(
                        command=command,
                        timestamp=time.time(),
                        reason=reason,
                        source='llm',
                        umo=event.unified_msg_origin
                    )
                    notice = (
                        f"🤖 LLM 尝试执行可能存在风险的指令：\n`{command}`\n\n"
                        f"判定原因: {reason}\n\n"
                        "⚠️ 该指令已被挂起，**工具链已暂停**。\n"
                        "若您确认允许 AI 执行此操作，请输入 `/shell_allow`，否则请输入 `/shell_deny`。"
                    )
                    # 发送消息给用户
                    await event.send(MessageChain([Plain(notice)]))
                    # 抛出 BaseException 子类来穿透框架的 except Exception 捕获，中断工具循环
                    raise ToolChainInterrupt(
                        f"命令需要管理员授权: {command}",
                        pending_tools=[]
                    )

        logger.info(f"LLM 请求执行命令: {command}")
        stdout, stderr, return_code = await self._execute_command(command)
        
        response = f"返回码: {return_code}"
        if stdout: response += f"\n输出:\n{stdout}"
        if stderr: response += f"\n错误:\n{stderr}"
        
        # 反馈给用户
        await event.send(MessageChain([Plain(f"LLM 执行了命令: `{command}`\n\n结果：\n{response}")]))
        return response

    @filter.command("send_file")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def send_file_command(self, event: AstrMessageEvent, path: str = ""):
        """根据路径发送文件的用户命令"""
        message_text = event.message_str.strip()
        parts = message_text.split(" ", 1)
        actual_path = parts[1].strip() if len(parts) > 1 else ""

        if not actual_path:
            yield event.plain_result("请提供文件路径。")
            return
        
        expanded_path = os.path.expanduser(actual_path)
        if not os.path.exists(expanded_path) or not os.path.isfile(expanded_path):
            yield event.plain_result(f"文件不存在或不是文件: {expanded_path}")
            return

        try:
            yield event.chain_result([File(name=os.path.basename(expanded_path), file=expanded_path)])
        except Exception as e:
            yield event.plain_result(f"发送失败: {e}")

    @filter.llm_tool(name="send_file_by_path")
    async def send_file_by_path(self, event: AstrMessageEvent, path: Optional[str] = None) -> str:
        """
        发送本地路径文件的 LLM 工具。
        
        Args:
            path(string): 要发送的文件的本地路径
        """
        if event.role != "admin": return "权限不足。"
        if not path: return "参数错误。"

        expanded_path = os.path.expanduser(path)
        if not os.path.exists(expanded_path): return f"文件未找到: {expanded_path}"
        
        try:
            await event.send(MessageChain([File(name=os.path.basename(expanded_path), file=expanded_path)]))
            return f"文件 {os.path.basename(expanded_path)} 已发送。"
        except Exception as e:
            return f"发送失败: {e}"

    @filter.llm_tool(name="send_file_by_url")
    async def send_file_by_url(self, event: AstrMessageEvent, url: Optional[str] = None) -> str:
        """
        根据 URL 发送文件的 LLM 工具。
        
        Args:
            url(string): 要下载并发送的文件的 URL 地址
        """
        if event.role != "admin": return "权限不足。"
        if not url: return "参数错误。"

        temp_file_path = os.path.join(self.working_directory, f"tmp_{uuid.uuid4()}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200: return f"下载失败: {resp.status}"
                    with open(temp_file_path, 'wb') as f:
                        f.write(await resp.read())

            await event.send(MessageChain([File(name="downloaded_file", file=temp_file_path)]))
            return "文件已下载并发送。"
        except Exception as e:
            return f"错误: {e}"
        finally:
            if os.path.exists(temp_file_path): os.remove(temp_file_path)
