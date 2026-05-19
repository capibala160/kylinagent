"""
MCP (Model Context Protocol) 协议核心数据结构
参考 https://modelcontextprotocol.io/specification
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class ToolInputSchema(BaseModel):
    type: str = "object"
    properties: Dict[str, Any] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)


class Tool(BaseModel):
    name: str
    description: str
    inputSchema: ToolInputSchema = Field(default_factory=ToolInputSchema)


class TextContent(BaseModel):
    type: str = "text"
    text: str


class ImageContent(BaseModel):
    type: str = "image"
    data: str
    mimeType: str


class EmbeddedResource(BaseModel):
    type: str = "resource"
    resource: Dict[str, Any]


ToolResultContent = Union[TextContent, ImageContent, EmbeddedResource]


class ToolCallRequest(BaseModel):
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    content: List[ToolResultContent] = Field(default_factory=list)
    isError: bool = False
    errorMessage: Optional[str] = None
    execution_time_ms: Optional[float] = None


class ServerCapability(BaseModel):
    tools: Dict[str, Any] = Field(default_factory=lambda: {"listChanged": False})


class ServerInfo(BaseModel):
    name: str
    version: str


class InitializeResult(BaseModel):
    protocolVersion: str = "2024-11-05"
    capabilities: ServerCapability = Field(default_factory=ServerCapability)
    serverInfo: ServerInfo


class Resource(BaseModel):
    uri: str
    name: str
    description: Optional[str] = None
    mimeType: Optional[str] = None


class ResourceContents(BaseModel):
    uri: str
    mimeType: Optional[str] = None
    text: Optional[str] = None
    blob: Optional[str] = None


class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Union[int, str]] = None
    method: str
    params: Optional[Dict[str, Any]] = None


class MCPResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Union[int, str]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
