import re
from dataclasses import dataclass
from typing import Callable, Generic, Optional, TypeVar

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

T = TypeVar('T', bound=Runnable)
U = TypeVar('U', bound=Runnable)
ModelRetryFunction = Callable[[T], U]

@dataclass
class RetryableModel(Generic[T, U]):
    model: T
    is_reasoning_model: bool
    retry_function: Optional[ModelRetryFunction[T, U]] = None

def _build_args(**kwargs):
    return { key: value for key, value in kwargs.items() if value is not None }

def get_model(model: str, 
              base_url: Optional[str] = None, 
              api_key: Optional[str] = None, 
              temperature: Optional[float] = None, 
              temperature_on_retry: Optional[float] = None, 
              seed: Optional[int] = None) -> RetryableModel:
    if model in ["gpt-4o-mini", "gpt-4o"]:
        return RetryableModel(
            model=ChatOpenAI(
                model=model,
                timeout=60,
                max_retries=20,
                **_build_args(
                    temperature=temperature,
                    base_url=base_url,
                    api_key=api_key,
                    seed=seed
                )
            ),
            retry_function=lambda x: x.bind(temperature=temperature_on_retry) if temperature_on_retry is not None else None,
            is_reasoning_model=False
        )
    if re.match(r"^o\d", model):
        return RetryableModel(
            model=ChatOpenAI(
                model=model,
                timeout=60,
                max_retries=20,
                temperature=1.0,
                **_build_args(
                    base_url=base_url,
                    api_key=api_key,
                    seed=seed
                )
            ),
            retry_function=None,
            is_reasoning_model=True
        )