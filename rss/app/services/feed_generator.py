from feedgen.feed import FeedGenerator
from datetime import datetime, timedelta
from ..core.config import settings
from ..models.entry import Entry
from typing import List
import logging
import os
import base64
import mimetypes
from pathlib import Path
import markdown  # 添加markdown包导入
import re
import json
logger = logging.getLogger(__name__)

class FeedService:
    
    
    @staticmethod
    def extract_telegram_title_and_content(content: str) -> tuple[str, str]:
        """从Telegram消息中提取标题和内容
        
        Args:
            content: 原始消息内容
            
        Returns:
            tuple: (标题, 剩余内容)
        """
        if not content:
            logger.info("输入内容为空,返回空标题和内容")
            return "", ""
            
        try:
            # 读取标题模板配置
            config_path = Path(__file__).parent.parent / 'configs' / 'title_template.json'
            logger.info(f"正在读取标题模板配置文件: {config_path}")
            with open(config_path, 'r', encoding='utf-8') as f:
                title_config = json.load(f)
                
            # 遍历每个模式
            for pattern_info in title_config['patterns']:
                pattern_str = pattern_info['pattern']
                pattern_desc = pattern_info['description']
                logger.debug(f"尝试匹配模式: {pattern_desc} ({pattern_str})")
                
                # 编译正则表达式
                pattern = re.compile(pattern_str, re.MULTILINE)
                
                # 尝试匹配
                match = pattern.match(content)
                if match:
                    title = FeedService.clean_title(match.group(1))
                    # 获取匹配部分的起始和结束位置
                    start, end = match.span(0)
                    # 提取剩余内容，去除开头的空白字符
                    remaining_content = content[end:].lstrip()
                    logger.info(f"成功匹配到标题模式: {pattern_desc}")
                    logger.info(f"原始内容: {content[:100]}...")  # 只显示前100个字符
                    logger.info(f"匹配模式: {pattern_str}")
                    logger.info(f"提取的标题: {title}")
                    logger.info(f"剩余内容长度: {len(remaining_content)} 字符")
                    return title, remaining_content
                    
            # 如果没有匹配到任何模式，使用前20个字符作为标题
            logger.info("未匹配到任何标题模式，使用前20个字符作为标题")
            # 去除内容中的换行符，并限制标题长度为20个字符
            clean_content = FeedService.clean_content(content)
            clean_content = clean_content.replace('\n', ' ').strip()
            title = clean_content[:20]
            if len(clean_content) > 20:
                title += "..."
            logger.debug(f"生成的默认标题: {title}")
            return title, content
            
        except Exception as e:
            logger.error(f"提取标题和内容时出错: {str(e)}")
            return "", content
    

    @staticmethod
    def clean_title(title: str) -> str:
        """清理标题中的特殊字符和格式标记
        
        Args:
            title: 原始标题文本
            
        Returns:
            str: 清理后的标题
        """
        if not title:
            return ""
            
        # 移除所有 * 号
        title = title.replace('*', '')
        
        # 处理链接格式 [text](url)，保留text部分
        title = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', title)
            
        # 移除换行和首尾空白
        title = title.replace('\n', ' ').strip()
        
        return title
    
    @staticmethod
    def clean_content(content: str) -> str:
        """清理内容中的特殊字符和格式标记
        
        Args:
            content: 原始内容文本
            
        Returns:
            str: 清理后的内容
        """
        if not content:
            return ""
            
        # 去除开头可能的1-2个星号
        content = re.sub(r'^\*{1,2}\s*', '', content)
        
        # 去除开头的空行
        content = re.sub(r'^\s*\n+', '', content)
        
        return content
    
    @staticmethod
    async def generate_feed_from_entries(rule_id: int, entries: List[Entry]) -> FeedGenerator:
        """根据真实条目生成Feed"""
        fg = FeedGenerator()
        # 设置编码
        fg.load_extension('base', atom=True)
        
        # 获取第一个条目作为Feed标题和描述的来源
        if entries:
            first_entry = entries[0]
            # 尝试获取频道名作为Feed标题
            chat_name = FeedService._extract_chat_name(first_entry.link)
            fg.title(f'TG Forwarder - {chat_name or f"Rule {rule_id}"}')
            fg.description(f'TG Forwarder RSS - 规则 {rule_id} 的消息')
        else:
            fg.title(f'TG Forwarder RSS - Rule {rule_id}')
            fg.description('TG Forwarder RSS Feed')
        
        # 设置Feed链接
        base_url = f"http://{settings.HOST}:{settings.PORT}"
        fg.link(href=f'{base_url}/rss/{rule_id}')
        fg.language('zh-CN')
        
        # 添加条目
        for entry in entries:
            try:
                fe = fg.add_entry()
                fe.id(entry.id or entry.message_id)
                
                # 提取标题和内容
                extracted_title, extracted_content = FeedService.extract_telegram_title_and_content(entry.content or "")
                fe.title(extracted_title)
                
                
                # 将提取的内容转换为HTML，保留原始换行结构
                content = FeedService.convert_markdown_to_html(extracted_content)
                
                # 添加图片 - 针对各种RSS阅读器的优化处理
                all_media_urls = []  # 存储所有媒体URL用于后续检查
                
                if entry.media:
                    # 处理每个媒体文件
                    for idx, media in enumerate(entry.media):
                        # 构建规范化的媒体URL - 恢复为包含规则ID的格式
                        media_filename = os.path.basename(media.url.split('/')[-1])
                        media_url = f"/media/{entry.rule_id}/{media_filename}"
                        full_media_url = f"{base_url}{media_url}"
                        all_media_urls.append(full_media_url)
                        
                        # 处理图片类型
                        if media.type.startswith('image/'):
                            try:
                                # 构建媒体文件路径
                                rule_media_path = settings.get_rule_media_path(entry.rule_id)
                                media_path = os.path.join(rule_media_path, media_filename)
                                
                                # 添加图片标签到内容中 - 使用包含规则ID的URL格式
                                img_tag = f'<p><img src="{full_media_url}" alt="{media.filename}" style="max-width:100%;height:auto;display:block;" /></p>'
                                content += img_tag
                                
                                logger.info(f"已添加图片标签到内容中: {media_filename}")
                            except Exception as e:
                                logger.error(f"添加图片标签时出错: {str(e)}")
                        elif media.type.startswith('video/'):
                            # 为视频添加特殊处理
                            display_name = ""
                            if hasattr(media, "original_name") and media.original_name:
                                display_name = media.original_name
                            else:
                                display_name = media.filename
                            
                            # 添加HTML5视频播放器 - 使用内联样式
                            video_player = f'''
                            <div style="margin:15px 0;border:1px solid #eee;padding:10px;border-radius:5px;background-color:#f9f9f9;">
                                <video controls width="100%" preload="none" poster="" style="width:100%;max-width:600px;display:block;margin:0 auto;">
                                    <source src="{full_media_url}" type="{media.type}">
                                    您的浏览器不支持HTML5视频播放
                                </video>
                                <p style="text-align:center;margin-top:8px;font-size:14px;">
                                    <a href="{full_media_url}" target="_blank">下载视频: {display_name}</a>
                                </p>
                            </div>
                            '''
                            content += video_player
                            
                            logger.info(f"添加视频播放器到内容中: {display_name}")
                        elif media.type.startswith('audio/'):
                            # 为音频添加特殊处理
                            display_name = ""
                            if hasattr(media, "original_name") and media.original_name:
                                display_name = media.original_name
                            else:
                                display_name = media.filename
                                
                            # 添加HTML5音频播放器 - 使用内联样式
                            audio_player = f'''
                            <div style="margin:15px 0;border:1px solid #eee;padding:10px;border-radius:5px;background-color:#f9f9f9;">
                                <audio controls style="width:100%;max-width:600px;display:block;margin:0 auto;">
                                    <source src="{full_media_url}" type="{media.type}">
                                    您的浏览器不支持HTML5音频播放
                                </audio>
                                <p style="text-align:center;margin-top:8px;font-size:14px;">
                                    <a href="{full_media_url}" target="_blank">下载音频: {display_name}</a>
                                </p>
                            </div>
                            '''
                            content += audio_player
                            
                            logger.info(f"添加音频播放器到内容中: {display_name}")
                        else:
                            # 其他类型文件添加下载链接
                            display_name = ""
                            if hasattr(media, "original_name") and media.original_name:
                                display_name = media.original_name
                            else:
                                display_name = media.filename
                            
                            # 添加美观的下载链接
                            file_tag = f'''
                            <div style="margin:15px 0;padding:10px;border-radius:5px;background-color:#f5f5f5;text-align:center;">
                                <a href="{full_media_url}" target="_blank" style="display:inline-block;padding:8px 16px;background-color:#4CAF50;color:white;text-decoration:none;border-radius:4px;">
                                    下载文件: {display_name}
                                </a>
                            </div>
                            '''
                            content += file_tag
                
                # 确保content不为空，至少包含一些默认文本
                if not content:
                    content = "<p>该消息没有文本内容。</p>"
                    if entry.media and len(entry.media) > 0:
                        content += f"<p>包含 {len(entry.media)} 个媒体文件。</p>"
                
                # 确保content是有效的HTML
                if not content.startswith("<"):
                    # 预处理文本中的换行符，确保段落结构
                    processed_content = ""
                    paragraphs = content.split("\n\n")
                    for p in paragraphs:
                        if p.strip():
                            lines = p.split("\n")
                            processed_content += f"<p>{lines[0]}"
                            for line in lines[1:]:
                                if line.strip():
                                    processed_content += f"<br>{line}"
                            processed_content += "</p>"
                    content = processed_content if processed_content else f"<p>{content}</p>"
                
                # 删除多余的HTML标签和空格，但保留有意义的段落结构
                content = re.sub(r'<br>\s*<br>', '<br>', content)
                content = re.sub(r'<p>\s*</p>', '', content)
                content = re.sub(r'<p><br></p>', '<p></p>', content)
                
                # 添加媒体附件，并确保内容中包含所有媒体
                if entry.media:
                    for media in entry.media:
                        try:
                            # 使用包含规则ID的媒体URL格式
                            media_filename = os.path.basename(media.url.split('/')[-1])
                            full_media_url = f"{base_url}/media/{entry.rule_id}/{media_filename}"
                            
                            # 确保图片等内容已经添加
                            if media.type.startswith('image/') and full_media_url not in content:
                                # 如果内容中没有该图片，添加
                                img_tag = f'<p><img src="{full_media_url}" alt="{media.filename}" style="max-width:100%;" /></p>'
                                content += img_tag
                                logger.info(f"添加缺失的图片标签: {media_filename}")
                            
                            # 记录添加的媒体附件
                            logger.info(f"添加媒体附件: {full_media_url}, 类型: {media.type}, 大小: {media.size}")
                            
                            # 添加enclosure
                            fe.enclosure(
                                url=full_media_url,
                                length=str(media.size) if hasattr(media, 'size') else "0",
                                type=media.type if hasattr(media, 'type') else "application/octet-stream"
                            )
                        except Exception as e:
                            logger.error(f"添加媒体附件时出错: {str(e)}")
                
                # 设置内容字段
                fe.content(content, type='html')
                
                # 设置描述字段 - 使用相同的内容
                fe.description(content)
                
                # 解析ISO格式时间字符串，设置发布时间
                try:
                    published_dt = datetime.fromisoformat(entry.published)
                    fe.published(published_dt)
                except ValueError:
                    # 如果时间格式无效，使用当前时间
                    fe.published(datetime.now(settings.DEFAULT_TIMEZONE))
                
                # 设置作者和链接
                if entry.author:
                    fe.author(name=entry.author)
                
                if entry.link:
                    fe.link(href=entry.link)
            except Exception as e:
                logger.error(f"添加条目到Feed时出错: {str(e)}")
                continue
        
        return fg
    
    @staticmethod
    def _extract_chat_name(link: str) -> str:
        """从Telegram链接中提取频道/群组名称"""
        if not link or 't.me/' not in link:
            return ""
        
        try:
            # 例如从 https://t.me/channel_name/1234 提取 channel_name
            parts = link.split('t.me/')
            if len(parts) < 2:
                return ""
            
            channel_part = parts[1].split('/')[0]
            return channel_part
        except Exception:
            return ""



    @staticmethod
    def convert_markdown_to_html(text):
        """将Markdown格式转换为HTML，使用标准markdown库，并保留换行结构"""
        if not text:
            return ""
        
        # 使用标准markdown库转换
        try:
            # 预处理文本，确保连续的换行符被正确转换成段落
            # 先将连续的多个换行替换为特殊标记
            text = re.sub(r'\n{2,}', '\n\n<!-- paragraph -->\n\n', text)
            
            # 转义以#开头的标签，防止被识别为标题
            lines = text.split('\n')
            processed_lines = []
            for line in lines:
                if line.startswith('#'):
                    line = '\\' + line
                processed_lines.append(line + '  ')  # 添加两个空格确保换行
            text = '\n'.join(processed_lines)
            
            # 使用markdown模块转换
            html = markdown.markdown(text, extensions=['extra'])
            
            # 处理特殊标记，确保段落分隔
            html = html.replace('<p><!-- paragraph --></p>', '</p><p>')
            
            return html
        except Exception as e:
            # 如果出现异常，退回到基本处理
            logger.error(f"Markdown转换异常: {str(e)}")
            
            # 改进的换行处理：将连续的两个或更多换行符转换为段落分隔
            text = re.sub(r'\n{2,}', '</p><p>', text)
            
            # 将单个换行符转换为<br>
            text = text.replace('\n', '<br>')
            
            return f"<p>{text}</p>"
    
    @staticmethod
    def generate_test_feed(rule_id: int) -> FeedGenerator:
        """生成测试Feed，当没有真实条目数据时使用
        
        Args:
            rule_id: 规则ID
            
        Returns:
            FeedGenerator: 配置好的测试Feed生成器
        """
        fg = FeedGenerator()
        # 设置编码
        fg.load_extension('base', atom=True)
        
        # 设置Feed基本信息
        fg.title(f'TG Forwarder RSS - Rule {rule_id} (测试数据)')
        fg.description(f'TG Forwarder RSS - 规则 {rule_id} 的测试消息')
        
        # 设置Feed链接
        base_url = f"http://{settings.HOST}:{settings.PORT}"
        fg.link(href=f'{base_url}/rss/feed/{rule_id}')
        fg.language('zh-CN')
        
        # 添加测试条目
        for i in range(1, 4):  # 生成3个测试条目
            fe = fg.add_entry()
            
            # 设置测试条目ID和标题
            entry_id = f"test-{rule_id}-{i}"
            fe.id(entry_id)
            fe.title(f"测试条目 {i} - 规则 {rule_id}")
            
            # 生成内容，包括测试说明
            content = f'''
            <p>这是一个测试条目，由系统自动生成，因为规则 {rule_id} 当前没有任何消息数据。</p>
            <p>当有消息被转发时，真实的条目将会在这里显示。</p>
            <hr>
            <p>此测试条目生成于: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            '''
            
            # 设置内容和描述
            fe.content(content, type='html')
            fe.description(content)
            
            # 设置测试条目的发布时间，依次递减，模拟时间顺序
            dt = datetime.now() - timedelta(hours=i)
            fe.published(dt)
            
            # 设置测试条目的作者和链接
            fe.author(name="TG Forwarder System")
            fe.link(href=f"{base_url}/rss/feed/{rule_id}?entry={entry_id}")
        
        return fg 