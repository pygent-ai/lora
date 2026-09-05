import asyncio
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from lora_api.models.requests import UpdateSettingsRequest
from lora_api.routers.settings import update_settings


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_project_path_does_not_reload_or_remember(tmp_path, kind):
    target = tmp_path / kind
    if kind == "file":
        target.write_text("not a directory", encoding="utf-8")
    context = Mock()
    with pytest.raises(HTTPException) as error:
        asyncio.run(update_settings(UpdateSettingsRequest(workspace_root=str(target)), context=context))
    assert error.value.status_code == 400
    context.areload.assert_not_called()
    context.remember_project.assert_not_called()
