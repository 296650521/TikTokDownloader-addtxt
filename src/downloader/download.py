from asyncio import Semaphore, gather
from datetime import datetime
from pathlib import Path
from shutil import move
from time import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Callable, Union

from aiofiles import open
from httpx import HTTPStatusError, RequestError, StreamError
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from ..custom import (
    MAX_WORKERS,
    PROGRESS,
)
from ..tools import (
    CacheError,
    DownloaderError,
    FakeProgress,
    Retry,
    beautify_string,
    format_size,
)
from ..translation import _

if TYPE_CHECKING:
    from httpx import AsyncClient

    from ..config import Parameter

__all__ = ["Downloader"]


class Downloader:
    semaphore = Semaphore(MAX_WORKERS)
    CONTENT_TYPE_MAP = {
        "image/png": "png",
        "image/jpeg": "jpeg",
        "image/webp": "webp",
        "video/mp4": "mp4",
        "video/quicktime": "mov",
        "audio/mp4": "m4a",
        "audio/mpeg": "mp3",
    }

    def __init__(
        self,
        params: "Parameter",
        server_mode: bool = False,
    ):
        self.cleaner = params.CLEANER
        self.client: "AsyncClient" = params.client
        self.client_tiktok: "AsyncClient" = params.client_tiktok
        self.headers = params.headers_download
        self.headers_tiktok = params.headers_download_tiktok
        self.log = params.logger
        self.xb = params.xb
        self.console = params.console
        self.root = params.root
        self.folder_name = params.folder_name
        self.name_format = params.name_format
        self.desc_length = params.desc_length
        self.name_length = params.name_length
        self.split = params.split
        self.folder_mode = params.folder_mode
        self.music = params.music
        self.dynamic_cover = params.dynamic_cover
        self.static_cover = params.static_cover
        # self.cookie = params.cookie
        # self.cookie_tiktok = params.cookie_tiktok
        self.proxy = params.proxy
        self.proxy_tiktok = params.proxy_tiktok
        self.download = params.download
        self.max_size = params.max_size
        self.chunk = params.chunk
        self.max_retry = params.max_retry
        self.recorder = params.recorder
        self.timeout = params.timeout
        self.ffmpeg = params.ffmpeg
        self.cache = params.cache
        self.truncate = params.truncate
        self.general_progress_object: Callable = self.init_general_progress(
            server_mode,
        )

    def init_general_progress(
        self,
        server_mode: bool = False,
    ) -> Callable:
        if server_mode:
            return self.__fake_progress_object
        return self.__general_progress_object

    @staticmethod
    def __fake_progress_object(
        *args,
        **kwargs,
    ):
        return FakeProgress()

    def __general_progress_object(self):
        """文件下载进度条"""
        return Progress(
            TextColumn(
                "[progress.description]{task.description}",
                style=PROGRESS,
                justify="left",
            ),
            SpinnerColumn(),
            BarColumn(bar_width=20),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            DownloadColumn(binary_units=True),
            "•",
            TimeRemainingColumn(),
            console=self.console,
            transient=True,
            expand=True,
        )

    def __live_progress_object(self):
        """直播下载进度条"""
        return Progress(
            TextColumn(
                "[progress.description]{task.description}",
                style=PROGRESS,
                justify="left",
            ),
            SpinnerColumn(),
            BarColumn(bar_width=20),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
            expand=True,
        )
    
    async def write_full_desc_to_txt(self, content: str, file_path: Path):
        """
        将完整作品文案写入TXT文件（UTF-8编码，避免中文乱码）
        :param content: 完整的原始文案
        :param file_path: 作品文件路径（视频/图集）
        """
        if not content:
            self.log.info(f"【TXT写入】{file_path.name} 无完整文案，跳过")
            return
        # 拼接TXT路径（与作品同目录、同名）
        txt_path = file_path.with_suffix(".txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(content.strip())
            self.log.info(f"【TXT写入】完整文案已保存至: {txt_path.resolve()}")
        except Exception as e:
            self.log.error(f"【TXT写入】失败: {e}")

    async def run(
        self,
        data: Union[list[dict], list[tuple]],
        type_: str,
        tiktok=False,
        **kwargs,
    ) -> None:
        if not self.download or not data:
            return
        self.log.info(_("开始下载作品文件"))
        match type_:
            case "batch":
                await self.run_batch(data, tiktok,** kwargs)
            case "detail":
                await self.run_general(data, tiktok, **kwargs)
            case "music":
                await self.run_music(data, **kwargs)
            case "live":
                await self.run_live(data, tiktok, **kwargs)
            case _:
                raise ValueError

    async def run_batch(
        self,
        data: list[dict],
        tiktok: bool,
        mode: str = "",
        mark: str = "",
        user_id: str = "",
        user_name: str = "",
        mix_id: str = "",
        mix_title: str = "",
        collect_id: str = "",
        collect_name: str = "",
    ):
        root = self.storage_folder(
            mode,
            *self.data_classification(
                mode,
                mark,
                user_id,
                user_name,
                mix_id,
                mix_title,
                collect_id,
                collect_name,
            ),
        )
        await self.batch_processing(
            data,
            root,
            tiktok=tiktok,
        )

    async def run_general(self, data: list[dict], tiktok: bool, **kwargs):
        root = self.storage_folder(mode="detail")
        await self.batch_processing(
            data,
            root,
            tiktok=tiktok,
        )

    async def run_music(
        self,
        data: list[dict],
        **kwargs,
    ):
        root = self.root.joinpath("Music")
        tasks = []
        for i in data:
            name = self.generate_music_name(i)
            temp_root, actual_root = self.deal_folder_path(
                root,
                name,
                False,
            )
            self.download_music(
                tasks,
                name,
                i["id"],
                i,
                temp_root,
                actual_root,
                "download",
                True,
                type_=_("音乐"),
            )
        await self.downloader_chart(
            tasks, SimpleNamespace(), self.general_progress_object(), **kwargs
        )

    async def run_live(
        self,
        data: list[tuple],
        tiktok=False,
        **kwargs,
    ):
        if not data or not self.download:
            return
        download_command = []
        self.generate_live_commands(
            data,
            download_command,
        )
        self.console.info(
            _("程序将会调用 ffmpeg 下载直播，关闭 DouK-Downloader 不会中断下载！"),
        )
        self.__download_live(download_command, tiktok)

    def generate_live_commands(
        self,
        data: list[tuple],
        commands: list,
        suffix: str = "mp4",
    ):
        root = self.root.joinpath("Live")
        root.mkdir(exist_ok=True)
        for i, f, m in data:
            name = self.cleaner.filter_name(
                f"{i['title']}{self.split}{i['nickname']}{self.split}{datetime.now():%Y-%m-%d %H.%M.%S}.{suffix}",
                f"{int(time())}{self.split}{datetime.now():%Y-%m-%d %H.%M.%S}.{suffix}",
            )
            path = root.joinpath(name)
            commands.append(
                (
                    m,
                    str(path.resolve()),
                )
            )

    def __download_live(
        self,
        commands: list,
        tiktok: bool,
    ):
        self.ffmpeg.download(
            commands,
            self.proxy_tiktok if tiktok else self.proxy,
            self.headers["User-Agent"],
        )

    async def batch_processing(self, data: list[dict], root: Path, **kwargs):
        count = SimpleNamespace(
            downloaded_image=set(),
            skipped_image=set(),
            downloaded_video=set(),
            skipped_video=set(),
            downloaded_live=set(),
            skipped_live=set(),
        )
        tasks = []
        for item in data:
            # 先备份完整文案到full_desc字段，再执行截断
            item["full_desc"] = item["desc"]
            item["desc"] = beautify_string(
                item["desc"],
                self.desc_length,
            )
            name = self.generate_detail_name(item)
            temp_root, actual_root = self.deal_folder_path(
                root,
                name,
                self.folder_mode,
            )
            params = {
                "tasks": tasks,
                "name": name,
                "id_": item["id"],
                "item": item,
                "temp_root": temp_root,
                "actual_root": actual_root,
            }
            if (t := item["type"]) == _("图集"):
                await self.download_image(
                    **params,
                    type_=_("图集"),
                    skipped=count.skipped_image,
                )
                # 图集下载后写入完整文案到TXT
                if item.get("full_desc") or item.get("desc"):
                    image_file_path = actual_root.with_name(f"{name}_1.jpeg")
                    await self.write_full_desc_to_txt(
                        content=item.get("full_desc", item.get("desc", "")),
                        file_path=image_file_path
                    )
            elif t == _("视频"):
                await self.download_video(
                    **params,
                    type_=_("视频"),
                    skipped=count.skipped_video,
                )
                # 视频下载后写入完整文案到TXT
                video_file_path = actual_root.with_name(f"{name}.mp4")
                await self.write_full_desc_to_txt(
                    content=item.get("full_desc", item.get("desc", "")),
                    file_path=video_file_path
                )
            elif t == _("实况"):
                await self.download_image(
                    suffix="mp4",
                    type_=_("实况"),
                    **params,
                    skipped=count.skipped_live,
                )
                # 实况下载后写入完整文案到TXT
                live_file_path = actual_root.with_name(f"{name}_1.mp4")
                await self.write_full_desc_to_txt(
                    content=item.get("full_desc", item.get("desc", "")),
                    file_path=live_file_path
                )
            else:
                raise DownloaderError
            self.download_music(
                **params,
                type=_("音乐"),
            )
            self.download_cover(**params)
        await self.downloader_chart(
            tasks, count, self.general_progress_object(), **kwargs
        )
        self.statistics_count(count)

    async def downloader_chart(
        self,
        tasks: list[tuple],
        count: SimpleNamespace,
        progress: Progress,
        semaphore: Semaphore = None,** kwargs,
    ):
        with progress:
            tasks = [
                self.request_file(
                    *task,
                    count=count,
                    **kwargs,
                    progress=progress,
                    semaphore=semaphore,
                )
                for task in tasks
            ]
            await gather(*tasks)

    def deal_folder_path(
        self,
        root: Path,
        name: str,
        folder_mode=False,
    ) -> tuple[Path, Path]:
        """生成文件的临时路径和目标路径"""
        root = self.create_detail_folder(root, name, folder_mode)
        root.mkdir(exist_ok=True)
        cache = self.cache.joinpath(name)
        actual = root.joinpath(name)
        return cache, actual

    async def is_downloaded(self, id_: str) -> bool:
        return await self.recorder.has_id(id_)

    @staticmethod
    def is_exists(path: Path) -> bool:
        return path.exists()

    async def is_skip(self, id_: str, path: Path) -> bool:
        return await self.is_downloaded(id_) or self.is_exists(path)

    async def download_image(
        self,
        tasks: list,
        name: str,
        id_: str,
        item: SimpleNamespace,
        skipped: set,
        temp_root: Path,
        actual_root: Path,
        suffix: str = "jpeg",
        type_: str = _("图集"),
    ) -> None:
        if not item["downloads"]:
            self.log.error(
                _("【{type}】{name} 提取文件下载地址失败，跳过下载").format(
                    type=type_, name=name
                )
            )
            return
        for index, img in enumerate(
            item["downloads"],
            start=1,
        ):
            if await self.is_downloaded(id_):
                skipped.add(id_)
                self.log.info(
                    _("【{type}】{name} 存在下载记录，跳过下载").format(
                        type=type_, name=name
                    )
                )
                break
            elif self.is_exists(p := actual_root.with_name(f"{name}_{index}.{suffix}")):
                self.log.info(
                    _("【{type}】{name}_{index} 文件已存在，跳过下载").format(
                        type=type_, name=name, index=index
                    )
                )
                self.log.info(f"文件路径: {p.resolve()}", False)
                skipped.add(id_)
                continue
            tasks.append(
                (
                    img,
                    temp_root.with_name(f"{name}_{index}.{suffix}"),
                    p,
                    f"【{type_}】{name}_{index}",
                    id_,
                    suffix,
                )
            )

    async def download_video(
        self,
        tasks: list,
        name: str,
        id_: str,
        item: SimpleNamespace,
        skipped: set,
        temp_root: Path,
        actual_root: Path,
        suffix: str = "mp4",
        type_: str = _("视频"),
    ) -> None:
        if not item["downloads"]:
            self.log.error(
                _("【{type}】{name} 提取文件下载地址失败，跳过下载").format(
                    type=type_, name=name
                )
            )
            return
        if await self.is_skip(
            id_,
            p := actual_root.with_name(
                f"{name}.{suffix}",
            ),
        ):
            self.log.info(
                _("【{type}】{name} 存在下载记录或文件已存在，跳过下载").format(
                    type=type_, name=name
                )
            )
            self.log.info(f"文件路径: {p.resolve()}", False)
            skipped.add(id_)
            return
        tasks.append(
            (
                item["downloads"],
                temp_root.with_name(f"{name}.{suffix}"),
                p,
                f"【{type_}】{name}",
                id_,
                suffix,
            )
        )

    def download_music(
        self,
        tasks: list,
        name: str,
        id_: str,
        item: dict,
        temp_root: Path,
        actual_root: Path,
        key: str = "music_url",
        switch: bool = False,
        suffix: str = "mp3",
        type_: str = _("音乐"),** kwargs,
    ) -> None:
        if self.check_deal_music(
            url := item[key],
            p := actual_root.with_name(f"{name}.{suffix}"),
            switch,
        ):
            tasks.append(
                (
                    url,
                    temp_root.with_name(f"{name}.{suffix}"),
                    p,
                    _("【{type}】{name}").format(
                        type=type_,
                        name=name,
                    ),
                    id_,
                    suffix,
                )
            )

    def download_cover(
        self,
        tasks: list,
        name: str,
        id_: str,
        item: SimpleNamespace,
        temp_root: Path,
        actual_root: Path,
        static_suffix: str = "jpeg",
        dynamic_suffix: str = "webp",** kwargs,
    ) -> None:
        if all(
            (
                self.static_cover,
                url := item["static_cover"],
                not self.is_exists(
                    p := actual_root.with_name(f"{name}.{static_suffix}")
                ),
            )
        ):
            tasks.append(
                (
                    url,
                    temp_root.with_name(f"{name}.{static_suffix}"),
                    p,
                    f"【封面】{name}",
                    id_,
                    static_suffix,
                )
            )
        if all(
            (
                self.dynamic_cover,
                url := item["dynamic_cover"],
                not self.is_exists(
                    p := actual_root.with_name(f"{name}.{dynamic_suffix}")
                ),
            )
        ):
            tasks.append(
                (
                    url,
                    temp_root.with_name(f"{name}.{dynamic_suffix}"),
                    p,
                    f"【动图】{name}",
                    id_,
                    dynamic_suffix,
                )
            )

    def check_deal_music(
        self,
        url: str,
        path: Path,
        switch=False,
    ) -> bool:
        """未传入 switch 参数则判断音乐下载开关设置"""
        return all((switch or self.music, url, not self.is_exists(path)))

    @Retry.retry
    async def request_file(
        self,
        url: str,
        temp: Path,
        actual: Path,
        show: str,
        id_: str,
        suffix: str,
        count: SimpleNamespace,
        progress: Progress,
        headers: dict = None,
        tiktok=False,
        unknown_size=False,
        semaphore: Semaphore = None,
    ) -> bool | None:
        async with semaphore or self.semaphore:
            client = self.client_tiktok if tiktok else self.client
            headers = self.__adapter_headers(
                headers,
                tiktok,
            )
            self.__record_request_messages(
                show,
                url,
                headers,
            )
            try:
                # length, suffix = await self.__head_file(client, url, headers, suffix, )
                position = self.__update_headers_range(
                    headers,
                    temp,
                )
                async with client.stream(
                    "GET",
                    url,
                    headers=headers,
                ) as response:
                    if response.status_code == 416:
                        raise CacheError(_("文件缓存异常，尝试重新下载"))
                    response.raise_for_status()
                    length, suffix = self._extract_content(
                        response.headers,
                        suffix,
                    )
                    length += position
                    self._record_response(
                        response,
                        show,
                        length,
                    )
                    match self._download_initial_check(
                        length,
                        unknown_size,
                        show,
                    ):
                        case 1:
                            return await self.download_file(
                                temp,
                                actual.with_suffix(
                                    f".{suffix}",
                                ),
                                show,
                                id_,
                                response,
                                length,
                                position,
                                count,
                                progress,
                            )
                        case 0:
                            return True
                        case -1:
                            return False
                        case _:
                            raise DownloaderError
            except RequestError as e:
                self.log.warning(_("网络异常: {error_repr}").format(error_repr=repr(e)))
                return False
            except HTTPStatusError as e:
                self.log.warning(
                    _("响应码异常: {error_repr}").format(error_repr=repr(e))
                )
                self.console.warning(
                    _(
                        "如果 TikTok 平台作品下载功能异常，请检查配置文件中 browser_info_tiktok 的 device_id 参数！"
                    ),
                )
                return False
            except CacheError as e:
                self.delete(temp)
                self.log.error(str(e))
                return False
            except Exception as e:
                self.log.error(_("下载异常: {error_repr}").format(error_repr=repr(e)))
                return False

    # 补充原文件缺失的基础方法（保证代码完整性）
    def __adapter_headers(self, headers: dict, tiktok: bool) -> dict:
        """适配请求头"""
        if headers:
            return headers
        return self.headers_tiktok if tiktok else self.headers

    def __record_request_messages(self, show: str, url: str, headers: dict):
        """记录请求日志"""
        self.log.debug(f"【下载请求】{show} - URL: {url}")
        self.log.debug(f"【请求头】{headers}", False)

    def __update_headers_range(self, headers: dict, temp: Path) -> int:
        """更新断点续传的Range请求头"""
        position = 0
        if temp.exists():
            position = temp.stat().st_size
            headers["Range"] = f"bytes={position}-"
        return position

    def _extract_content(self, headers: dict, suffix: str) -> tuple[int, str]:
        """提取响应内容长度和后缀"""
        length = int(headers.get("content-length", 0))
        content_type = headers.get("content-type", "")
        if content_type in self.CONTENT_TYPE_MAP:
            suffix = self.CONTENT_TYPE_MAP[content_type]
        return length, suffix

    def _record_response(self, response, show: str, length: int):
        """记录响应日志"""
        self.log.debug(f"【下载响应】{show} - 状态码: {response.status_code}")
        self.log.debug(f"【文件大小】{format_size(length)}", False)

    def _download_initial_check(self, length: int, unknown_size: bool, show: str) -> int:
        """下载前初始检查"""
        if length > self.max_size * 1024 * 1024 and self.max_size != 0:
            self.log.warning(
                _("【{show}】文件大小超出限制 {size}，跳过下载").format(
                    show=show, size=format_size(self.max_size * 1024 * 1024)
                )
            )
            return -1
        if length == 0 and not unknown_size:
            self.log.warning(_("【{show}】文件大小为0，跳过下载").format(show=show))
            return 0
        return 1

    async def download_file(
        self,
        temp: Path,
        actual: Path,
        show: str,
        id_: str,
        response,
        length: int,
        position: int,
        count: SimpleNamespace,
        progress: Progress,
    ) -> bool:
        """下载文件核心逻辑"""
        task = progress.add_task(show, total=length, completed=position)
        async with open(temp, "ab") as f:
            async for chunk in response.aiter_bytes(chunk_size=self.chunk):
                await f.write(chunk)
                progress.update(task, advance=len(chunk))
        progress.remove_task(task)
        try:
            move(temp, actual)
        except Exception as e:
            self.log.error(_("文件移动失败: {error}").format(error=e))
            return False
        await self.recorder.record_id(id_)
        self.log.info(_("【{show}】下载完成: {path}").format(show=show, path=actual.resolve()))
        # 统计下载数量
        if "图集" in show or "实况" in show:
            count.downloaded_image.add(id_)
        elif "视频" in show:
            count.downloaded_video.add(id_)
        elif "直播" in show:
            count.downloaded_live.add(id_)
        return True

    def delete(self, path: Path):
        """删除文件"""
        if path.exists():
            path.unlink()

    def statistics_count(self, count: SimpleNamespace):
        """统计下载数量"""
        self.log.info(_("下载完成统计："))
        self.log.info(_("视频 - 下载: {0} 跳过: {1}").format(len(count.downloaded_video), len(count.skipped_video)))
        self.log.info(_("图集/实况 - 下载: {0} 跳过: {1}").format(len(count.downloaded_image), len(count.skipped_image)))
        self.log.info(_("直播 - 下载: {0} 跳过: {1}").format(len(count.downloaded_live), len(count.skipped_live)))

    def storage_folder(self, mode: str, *args) -> Path:
        """生成存储文件夹路径（适配原项目逻辑）"""
        if mode == "detail":
            return self.root.joinpath(self.folder_name)
        return self.root.joinpath(self.folder_name, *args)

    def data_classification(self, mode: str, *args) -> tuple:
        """数据分类（适配原项目逻辑）"""
        return args[:-1] if mode in ["user", "mix", "collect"] else args

    def create_detail_folder(self, root: Path, name: str, folder_mode: bool) -> Path:
        """创建详情文件夹（适配原项目逻辑）"""
        if folder_mode:
            root = root.joinpath(name)
        return root

    def generate_detail_name(self, item: dict) -> str:
        """生成作品详情名称（适配原项目逻辑）"""
        name = self.name_format.format(
            id=item["id"],
            desc=item["desc"],
            author=item.get("author", ""),
            time=datetime.fromtimestamp(item.get("create_time", 0)).strftime("%Y%m%d%H%M%S"),
        )
        return self.cleaner.filter_name(
            name[:self.name_length],
            f"{item['id']}_{int(time())}",
        )

    def generate_music_name(self, item: dict) -> str:
        """生成音乐名称（适配原项目逻辑）"""
        name = f"{item.get('music_name', '')}{self.split}{item.get('author', '')}"
        return self.cleaner.filter_name(
            name[:self.name_length],
            f"{item['id']}_{int(time())}",
        )
