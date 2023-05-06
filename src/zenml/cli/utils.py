#  Copyright (c) ZenML GmbH 2020. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at:
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
#  or implied. See the License for the specific language governing
#  permissions and limitations under the License.
"""Utility functions for the CLI."""
import contextlib
import datetime
import os
import subprocess
import sys
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    NoReturn,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
)

import click
from pydantic import SecretStr
from rich import box, table
from rich.emoji import Emoji
from rich.markdown import Markdown
from rich.markup import escape
from rich.prompt import Confirm
from rich.style import Style

from zenml.config.global_config import GlobalConfiguration
from zenml.console import console, zenml_style_defaults
from zenml.constants import FILTERING_DATETIME_FORMAT, IS_DEBUG_ENV
from zenml.enums import GenericFilterOps, StackComponentType, StoreType
from zenml.logger import get_logger
from zenml.model_registries.base_model_registry import (
    ModelVersion,
    RegisteredModel,
)
from zenml.models import BaseFilterModel
from zenml.models.base_models import BaseResponseModel
from zenml.models.filter_models import (
    BoolFilter,
    NumericFilter,
    StrFilter,
    UUIDFilter,
)
from zenml.models.page_model import Page
from zenml.secret import BaseSecretSchema
from zenml.services import BaseService, ServiceState
from zenml.stack import StackComponent
from zenml.stack.stack_component import StackComponentConfig
from zenml.utils import secret_utils
from zenml.zen_server.deploy import ServerDeployment

logger = get_logger(__name__)

if TYPE_CHECKING:
    from uuid import UUID

    from rich.text import Text

    from zenml.client import Client
    from zenml.enums import ExecutionStatus
    from zenml.integrations.integration import Integration
    from zenml.model_deployers import BaseModelDeployer
    from zenml.models import (
        AuthenticationMethodModel,
        ComponentResponseModel,
        FlavorResponseModel,
        PipelineRunResponseModel,
        ResourceTypeModel,
        ServiceConnectorRequestModel,
        ServiceConnectorResourcesModel,
        ServiceConnectorResponseModel,
        ServiceConnectorTypeModel,
        StackResponseModel,
    )
    from zenml.stack import Stack

MAX_ARGUMENT_VALUE_SIZE = 10240


def title(text: str) -> None:
    """Echo a title formatted string on the CLI.

    Args:
        text: Input text string.
    """
    console.print(text.upper(), style=zenml_style_defaults["title"])


def confirmation(text: str, *args: Any, **kwargs: Any) -> bool:
    """Echo a confirmation string on the CLI.

    Args:
        text: Input text string.
        *args: Args to be passed to click.confirm().
        **kwargs: Kwargs to be passed to click.confirm().

    Returns:
        Boolean based on user response.
    """
    return Confirm.ask(text, console=console)


def declare(
    text: Union[str, "Text"],
    bold: Optional[bool] = None,
    italic: Optional[bool] = None,
    **kwargs: Any,
) -> None:
    """Echo a declaration on the CLI.

    Args:
        text: Input text string.
        bold: Optional boolean to bold the text.
        italic: Optional boolean to italicize the text.
        **kwargs: Optional kwargs to be passed to console.print().
    """
    base_style = zenml_style_defaults["info"]
    style = Style.chain(base_style, Style(bold=bold, italic=italic))
    console.print(text, style=style, **kwargs)


def error(text: str) -> NoReturn:
    """Echo an error string on the CLI.

    Args:
        text: Input text string.

    Raises:
        ClickException: when called.
    """
    raise click.ClickException(message=click.style(text, fg="red", bold=True))


def warning(
    text: str,
    bold: Optional[bool] = None,
    italic: Optional[bool] = None,
    **kwargs: Any,
) -> None:
    """Echo a warning string on the CLI.

    Args:
        text: Input text string.
        bold: Optional boolean to bold the text.
        italic: Optional boolean to italicize the text.
        **kwargs: Optional kwargs to be passed to console.print().
    """
    base_style = zenml_style_defaults["warning"]
    style = Style.chain(base_style, Style(bold=bold, italic=italic))
    console.print(text, style=style, **kwargs)


def print_table(
    obj: List[Dict[str, Any]],
    title: Optional[str] = None,
    caption: Optional[str] = None,
    **columns: table.Column,
) -> None:
    """Prints the list of dicts in a table format.

    The input object should be a List of Dicts. Each item in that list represent
    a line in the Table. Each dict should have the same keys. The keys of the
    dict will be used as headers of the resulting table.

    Args:
        obj: A List containing dictionaries.
        title: Title of the table.
        caption: Caption of the table.
        columns: Optional column configurations to be used in the table.
    """
    column_keys = {key: None for dict_ in obj for key in dict_}
    column_names = [columns.get(key, key.upper()) for key in column_keys]
    rich_table = table.Table(
        box=box.HEAVY_EDGE, show_lines=True, title=title, caption=caption
    )
    for col_name in column_names:
        if isinstance(col_name, str):
            rich_table.add_column(str(col_name), overflow="fold")
        else:
            rich_table.add_column(
                str(col_name.header).upper(), overflow="fold"
            )
    for dict_ in obj:
        values = []
        for key in column_keys:
            if key is None:
                values.append(None)
            else:
                value = str(dict_.get(key) or " ")
                # escape text when square brackets are used
                if "[" in value:
                    value = escape(value)
                values.append(value)
        rich_table.add_row(*values)
    if len(rich_table.columns) > 1:
        rich_table.columns[0].justify = "center"
    console.print(rich_table)


T = TypeVar("T", bound=BaseResponseModel)


def print_pydantic_models(
    models: Union[Page[T], List[T]],
    columns: Optional[List[str]] = None,
    exclude_columns: Optional[List[str]] = None,
    is_active: Optional[Callable[[T], bool]] = None,
) -> None:
    """Prints the list of Pydantic models in a table.

    Args:
        models: List of Pydantic models that will be represented as a row in
            the table.
        columns: Optionally specify subset and order of columns to display.
        exclude_columns: Optionally specify columns to exclude. (Note: `columns`
            takes precedence over `exclude_columns`.)
        is_active: Optional function that marks as row as active.

    """
    if exclude_columns is None:
        exclude_columns = list()

    def __dictify(model: T) -> Dict[str, str]:
        """Helper function to map over the list to turn Models into dicts.

        Args:
            model: Pydantic model.

        Returns:
            Dict of model attributes.
        """
        # Explicitly defined columns take precedence over exclude columns
        if not columns:
            include_columns = [
                k for k in model.dict().keys() if k not in exclude_columns  # type: ignore[operator]
            ]
        else:
            include_columns = columns

        items: Dict[str, Any] = {}

        for k in include_columns:
            value = getattr(model, k)
            # In case the response model contains nested `BaseResponseModels`
            #  we want to attempt to represent them by name, if they contain
            #  such a field, else the id is used
            if isinstance(value, BaseResponseModel):
                if "name" in value.__fields__:
                    items[k] = str(value.name)  # type: ignore[attr-defined]
                else:
                    items[k] = str(value.id)

            # If it is a list of `BaseResponseModels` access each Model within
            #  the list and extract either name or id
            elif isinstance(value, list) and issubclass(
                model.__fields__[k].type_, BaseResponseModel
            ):
                for v in value:
                    if "name" in v.__fields__:
                        items.setdefault(k, []).append(str(v.name))
                    else:
                        items.setdefault(k, []).append(str(v.id))
            elif isinstance(value, Set) or isinstance(value, List):
                items[k] = [str(v) for v in value]
            else:
                items[k] = str(value)
        # prepend an active marker if a function to mark active was passed
        marker = "active"
        if marker in items:
            marker = "current"
        return (
            {marker: ":point_right:" if is_active(model) else "", **items}
            if is_active is not None
            else items
        )

    if isinstance(models, Page):
        print_table([__dictify(model) for model in models.items])
        print_page_info(models)
    else:
        print_table([__dictify(model) for model in models])


def format_integration_list(
    integrations: List[Tuple[str, Type["Integration"]]]
) -> List[Dict[str, str]]:
    """Formats a list of integrations into a List of Dicts.

    This list of dicts can then be printed in a table style using
    cli_utils.print_table.

    Args:
        integrations: List of tuples containing the name of the integration and
            the integration metadata.

    Returns:
        List of Dicts containing the name of the integration and the integration
    """
    list_of_dicts = []
    for name, integration_impl in integrations:
        is_installed = integration_impl.check_installation()
        list_of_dicts.append(
            {
                "INSTALLED": ":white_check_mark:" if is_installed else ":x:",
                "INTEGRATION": name,
                "REQUIRED_PACKAGES": ", ".join(
                    integration_impl.get_requirements()
                ),
            }
        )
    return list_of_dicts


def print_stack_configuration(
    stack: "StackResponseModel", active: bool
) -> None:
    """Prints the configuration options of a stack.

    Args:
        stack: Instance of a stack model.
        active: Whether the stack is active.
    """
    stack_caption = f"'{stack.name}' stack"
    if active:
        stack_caption += " (ACTIVE)"
    rich_table = table.Table(
        box=box.HEAVY_EDGE,
        title="Stack Configuration",
        caption=stack_caption,
        show_lines=True,
    )
    rich_table.add_column("COMPONENT_TYPE", overflow="fold")
    rich_table.add_column("COMPONENT_NAME", overflow="fold")
    for component_type, components in stack.components.items():
        rich_table.add_row(component_type, components[0].name)

    # capitalize entries in first column
    rich_table.columns[0]._cells = [
        component.upper()  # type: ignore[union-attr]
        for component in rich_table.columns[0]._cells
    ]
    console.print(rich_table)
    declare(
        f"Stack '{stack.name}' with id '{stack.id}' is "
        f"{f'owned by user {stack.user.name} and is ' if stack.user else ''}"
        f"'{'shared' if stack.is_shared else 'private'}'."
    )


def print_flavor_list(flavors: Page["FlavorResponseModel"]) -> None:
    """Prints the list of flavors.

    Args:
        flavors: List of flavors to print.
    """
    flavor_table = []
    for f in flavors.items:
        flavor_table.append(
            {
                "FLAVOR": f.name,
                "INTEGRATION": f.integration,
                "SOURCE": f.source,
            }
        )

    print_table(flavor_table)


def print_stack_component_configuration(
    component: "ComponentResponseModel", active_status: bool
) -> None:
    """Prints the configuration options of a stack component.

    Args:
        component: The stack component to print.
        active_status: Whether the stack component is active.
    """
    if component.user:
        user_name = component.user.name
    else:
        user_name = "[DELETED]"

    declare(
        f"{component.type.value.title()} '{component.name}' of flavor "
        f"'{component.flavor}' with id '{component.id}' is owned by "
        f"user '{user_name}' and is "
        f"'{'shared' if component.is_shared else 'private'}'."
    )

    if len(component.configuration) == 0:
        declare("No configuration options are set for this component.")

    else:
        title_ = (
            f"'{component.name}' {component.type.value.upper()} "
            f"Component Configuration"
        )

        if active_status:
            title_ += " (ACTIVE)"
        rich_table = table.Table(
            box=box.HEAVY_EDGE,
            title=title_,
            show_lines=True,
        )
        rich_table.add_column("COMPONENT_PROPERTY")
        rich_table.add_column("VALUE", overflow="fold")

        component_dict = component.dict()
        component_dict.pop("configuration")
        component_dict.update(component.configuration)

        items = component.configuration.items()
        for item in items:
            elements = []
            for idx, elem in enumerate(item):
                if idx == 0:
                    elements.append(f"{elem.upper()}")
                else:
                    elements.append(str(elem))
            rich_table.add_row(*elements)

        console.print(rich_table)

    if not component.labels:
        declare("No labels are set for this component.")
    else:
        rich_table = table.Table(
            box=box.HEAVY_EDGE,
            title="Labels",
            show_lines=True,
        )
        rich_table.add_column("LABEL")
        rich_table.add_column("VALUE", overflow="fold")

        for label, value in component.labels.items():
            rich_table.add_row(label, value)

        console.print(rich_table)

    if not component.connector:
        declare("No connector is set for this component.")
    else:
        rich_table = table.Table(
            box=box.HEAVY_EDGE,
            title="Service Connector",
            show_lines=True,
        )
        rich_table.add_column("PROPERTY")
        rich_table.add_column("VALUE", overflow="fold")

        connector_dict = {
            "ID": str(component.connector.id),
            "NAME": component.connector.name,
            "TYPE": component.connector.type,
            "RESOURCE TYPE": component.connector.resource_types[0],
            "RESOURCE NAME": component.connector_resource_id
            or component.connector.resource_id
            or "N/A",
        }

        for label, value in connector_dict.items():
            rich_table.add_row(label, value)

        console.print(rich_table)


def print_active_config() -> None:
    """Print the active configuration."""
    from zenml.client import Client

    gc = GlobalConfiguration()
    client = Client()

    # We use gc.store here instead of client.zen_store for two reasons:
    # 1. to avoid initializing ZenML with the default store just because we want
    # to print the active config
    # 2. to avoid connecting to the active store and keep this call lightweight
    if not gc.store:
        return

    if gc.uses_default_store():
        declare("Using the default local database.")
    elif gc.store.type == StoreType.SQL:
        declare(f"Using the SQL database: '{gc.store.url}'.")
    elif gc.store.type == StoreType.REST:
        declare(f"Connected to the ZenML server: '{gc.store.url}'")
    if client.uses_local_configuration:
        declare(
            f"Running with active workspace: '{client.active_workspace.name}' "
            "(repository)"
        )
    else:
        declare(
            f"Running with active workspace: '{gc.get_active_workspace_name()}' "
            "(global)"
        )


def print_active_stack() -> None:
    """Print active stack."""
    from zenml.client import Client

    client = Client()
    scope = "repository" if client.uses_local_configuration else "global"
    declare(
        f"Running with active stack: '{client.active_stack_model.name}' "
        f"({scope})"
    )


def expand_argument_value_from_file(name: str, value: str) -> str:
    """Expands the value of an argument pointing to a file into the contents of that file.

    Args:
        name: Name of the argument. Used solely for logging purposes.
        value: The value of the argument. This is to be interpreted as a
            filename if it begins with a `@` character.

    Returns:
        The argument value expanded into the contents of the file, if the
        argument value begins with a `@` character. Otherwise, the argument
        value is returned unchanged.

    Raises:
        ValueError: If the argument value points to a file that doesn't exist,
            that cannot be read, or is too long(i.e. exceeds
            `MAX_ARGUMENT_VALUE_SIZE` bytes).
    """
    if value.startswith("@@"):
        return value[1:]
    if not value.startswith("@"):
        return value
    filename = os.path.abspath(os.path.expanduser(value[1:]))
    logger.info(
        f"Expanding argument value `{name}` to contents of file `{filename}`."
    )
    if not os.path.isfile(filename):
        raise ValueError(
            f"Could not load argument '{name}' value: file "
            f"'{filename}' does not exist or is not readable."
        )
    try:
        if os.path.getsize(filename) > MAX_ARGUMENT_VALUE_SIZE:
            raise ValueError(
                f"Could not load argument '{name}' value: file "
                f"'{filename}' is too large (max size is "
                f"{MAX_ARGUMENT_VALUE_SIZE} bytes)."
            )

        with open(filename, "r") as f:
            return f.read()
    except OSError as e:
        raise ValueError(
            f"Could not load argument '{name}' value: file "
            f"'{filename}' could not be accessed: {str(e)}"
        )


def parse_name_and_extra_arguments(
    args: List[str],
    expand_args: bool = False,
    name_mandatory: bool = True,
) -> Tuple[Optional[str], Dict[str, str]]:
    """Parse a name and extra arguments from the CLI.

    This is a utility function used to parse a variable list of optional CLI
    arguments of the form `--key=value` that must also include one mandatory
    free-form name argument. There is no restriction as to the order of the
    arguments.

    Examples:
        >>> parse_name_and_extra_arguments(['foo']])
        ('foo', {})
        >>> parse_name_and_extra_arguments(['foo', '--bar=1'])
        ('foo', {'bar': '1'})
        >>> parse_name_and_extra_arguments(['--bar=1', 'foo', '--baz=2'])
        ('foo', {'bar': '1', 'baz': '2'})
        >>> parse_name_and_extra_arguments(['--bar=1'])
        Traceback (most recent call last):
            ...
            ValueError: Missing required argument: name

    Args:
        args: A list of command line arguments from the CLI.
        expand_args: Whether to expand argument values into the contents of the
            files they may be pointing at using the special `@` character.
        name_mandatory: Whether the name argument is mandatory.

    Returns:
        The name and a dict of parsed args.
    """
    name: Optional[str] = None
    # The name was not supplied as the first argument, we have to
    # search the other arguments for the name.
    for i, arg in enumerate(args):
        if not arg:
            # Skip empty arguments.
            continue
        if arg.startswith("--"):
            continue
        name = args.pop(i)
        break
    else:
        if name_mandatory:
            error(
                "A name must be supplied. Please see the command help for more "
                "information."
            )

    message = (
        "Please provide args with a proper "
        "identifier as the key and the following structure: "
        '--custom_argument="value"'
    )
    args_dict: Dict[str, str] = {}
    for a in args:
        if not a:
            # Skip empty arguments.
            continue
        if not a.startswith("--") or "=" not in a:
            error(f"Invalid argument: '{a}'. {message}")
        key, value = a[2:].split("=", maxsplit=1)
        if not key.isidentifier():
            error(f"Invalid argument: '{a}'. {message}")
        args_dict[key] = value

    if expand_args:
        args_dict = {
            k: expand_argument_value_from_file(k, v)
            for k, v in args_dict.items()
        }

    return name, args_dict


def parse_unknown_component_attributes(args: List[str]) -> List[str]:
    """Parse unknown options from the CLI.

    Args:
        args: A list of strings from the CLI.

    Returns:
        List of parsed args.
    """
    warning_message = (
        "Please provide args with a proper "
        "identifier as the key and the following structure: "
        "--custom_attribute"
    )

    assert all(a.startswith("--") for a in args), warning_message
    p_args = [a.lstrip("-") for a in args]
    assert all(v.isidentifier() for v in p_args), warning_message
    return p_args


def prompt_configuration(
    config_schema: Dict[str, Any],
    show_secrets: bool = False,
    existing_config: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Prompt the user for configuration values using the provided schema.

    Args:
        config_schema: The configuration schema.
        show_secrets: Whether to show secrets in the terminal.
        existing_config: The existing configuration values.

    Returns:
        The configuration values provided by the user.
    """
    is_update = False
    if existing_config is not None:
        is_update = True
    existing_config = existing_config or {}

    config_dict = {}
    for attr_name, attr_schema in config_schema.get("properties", {}).items():
        title = attr_schema.get("title", attr_name)
        attr_type = attr_schema.get("type", "string")
        title = f"[{attr_name}] {title}"
        required = attr_name in config_schema.get("required", [])
        hidden = attr_schema.get("format", "") == "password"
        subtitles: List[str] = []
        subtitles.append(attr_type)
        if hidden:
            subtitles.append("secret")
        if required:
            subtitles.append("required")
        else:
            subtitles.append("optional")
        if subtitles:
            title += f" {{{', '.join(subtitles)}}}"

        existing_value = existing_config.get(attr_name)
        if is_update:
            if existing_value:
                if isinstance(existing_value, SecretStr):
                    existing_value = existing_value.get_secret_value()
                if hidden and not show_secrets:
                    title += " is currently set to: [HIDDEN]"
                else:
                    title += f" is currently set to: '{existing_value}'"
            else:
                title += " is not currently set"

            click.echo(title)

            if existing_value:
                title = (
                    "Please enter a new value or press Enter to keep the "
                    "existing one"
                )
            elif required:
                title = "Please enter a new value"
            else:
                title = "Please enter a new value or press Enter to skip it"

        while True:
            # Ask the user to enter a value for the attribute
            value = click.prompt(
                title,
                type=str,
                hide_input=hidden and not show_secrets,
                default=existing_value or ("" if not required else None),
                show_default=False,
            )
            if not value:
                if required:
                    warning(
                        f"The attribute '{title}' is mandatory. "
                        "Please enter a non-empty value."
                    )
                    continue
                else:
                    value = None
                    break
            else:
                break

        if (
            is_update
            and value is not None
            and value == existing_value
            and not required
        ):
            confirm = click.confirm(
                "You left this optional attribute unchanged. Would you "
                "like to remove its value instead?",
                default=False,
            )
            if confirm:
                value = None

        if value:
            config_dict[attr_name] = value

    return config_dict


def install_packages(
    packages: List[str],
    upgrade: bool = False,
) -> None:
    """Installs pypi packages into the current environment with pip.

    Args:
        packages: List of packages to install.
        upgrade: Whether to upgrade the packages if they are already installed.
    """
    if upgrade:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
        ] + packages
    else:
        command = [sys.executable, "-m", "pip", "install"] + packages

    if not IS_DEBUG_ENV:
        command += [
            "-qqq",
            "--no-warn-conflicts",
        ]

    subprocess.check_call(command)


def uninstall_package(package: str) -> None:
    """Uninstalls pypi package from the current environment with pip.

    Args:
        package: The package to uninstall.
    """
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "uninstall",
            "-qqq",
            "-y",
            package,
        ]
    )


def pretty_print_secret(
    secret: "Union[BaseSecretSchema, Dict[str, str]]",
    hide_secret: bool = True,
    print_name: bool = False,
) -> None:
    """Given a secret with values, print all key-value pairs associated with the secret.

    Args:
        secret: Secret of type BaseSecretSchema
        hide_secret: boolean that configures if the secret values are shown
            on the CLI
        print_name: boolean that configures if the secret name is shown on the
            CLI
    """
    title: Optional[str] = None
    if isinstance(secret, BaseSecretSchema):
        if print_name:
            title = f"Secret: {secret.name}"
        secret = secret.content

    def get_secret_value(value: Any) -> str:
        if value is None:
            return ""
        return "***" if hide_secret else str(value)

    stack_dicts = [
        {
            "SECRET_KEY": key,
            "SECRET_VALUE": get_secret_value(value),
        }
        for key, value in secret.items()
    ]

    print_table(stack_dicts, title=title)


def print_list_items(list_items: List[str], column_title: str) -> None:
    """Prints the configuration options of a stack.

    Args:
        list_items: List of items
        column_title: Title of the column
    """
    rich_table = table.Table(
        box=box.HEAVY_EDGE,
        show_lines=True,
    )
    rich_table.add_column(column_title.upper(), overflow="fold")
    list_items.sort()
    for item in list_items:
        rich_table.add_row(item)

    console.print(rich_table)


def get_service_state_emoji(state: "ServiceState") -> str:
    """Get the rich emoji representing the operational state of a Service.

    Args:
        state: Service state to get emoji for.

    Returns:
        String representing the emoji.
    """
    from zenml.services.service_status import ServiceState

    if state == ServiceState.ACTIVE:
        return ":white_check_mark:"
    if state == ServiceState.INACTIVE:
        return ":pause_button:"
    if state == ServiceState.ERROR:
        return ":heavy_exclamation_mark:"
    return ":hourglass_not_done:"


def pretty_print_model_deployer(
    model_services: List["BaseService"], model_deployer: "BaseModelDeployer"
) -> None:
    """Given a list of served_models, print all associated key-value pairs.

    Args:
        model_services: list of model deployment services
        model_deployer: Active model deployer
    """
    model_service_dicts = []
    for model_service in model_services:
        served_model_info = model_deployer.get_model_server_info(model_service)
        dict_uuid = str(model_service.uuid)
        dict_pl_name = model_service.config.pipeline_name
        dict_pl_stp_name = model_service.config.pipeline_step_name
        dict_model_name = served_model_info.get("MODEL_NAME", "")
        model_service_dicts.append(
            {
                "STATUS": get_service_state_emoji(model_service.status.state),
                "UUID": dict_uuid,
                "PIPELINE_NAME": dict_pl_name,
                "PIPELINE_STEP_NAME": dict_pl_stp_name,
                "MODEL_NAME": dict_model_name,
            }
        )
    print_table(
        model_service_dicts, UUID=table.Column(header="UUID", min_width=36)
    )


def pretty_print_registered_model_table(
    registered_models: List["RegisteredModel"],
) -> None:
    """Given a list of registered_models, print all associated key-value pairs.

    Args:
        registered_models: list of registered models
    """
    registered_model_dicts = [
        {
            "NAME": registered_model.name,
            "DESCRIPTION": registered_model.description,
            "METADATA": registered_model.metadata,
        }
        for registered_model in registered_models
    ]
    print_table(
        registered_model_dicts, UUID=table.Column(header="UUID", min_width=36)
    )


def pretty_print_model_version_table(
    model_versions: List["ModelVersion"],
) -> None:
    """Given a list of model_versions, print all associated key-value pairs.

    Args:
        model_versions: list of model versions
    """
    model_version_dicts = [
        {
            "NAME": model_version.registered_model.name,
            "MODEL_VERSION": model_version.version,
            "VERSION_DESCRIPTION": model_version.description,
            "METADATA": model_version.metadata.dict()
            if model_version.metadata
            else {},
        }
        for model_version in model_versions
    ]
    print_table(
        model_version_dicts, UUID=table.Column(header="UUID", min_width=36)
    )


def pretty_print_model_version_details(
    model_version: "ModelVersion",
) -> None:
    """Given a model_version, print all associated key-value pairs.

    Args:
        model_version: model version
    """
    title_ = f"Properties of model `{model_version.registered_model.name}` version `{model_version.version}`"

    rich_table = table.Table(
        box=box.HEAVY_EDGE,
        title=title_,
        show_lines=True,
    )
    rich_table.add_column("MODEL VERSION PROPERTY", overflow="fold")
    rich_table.add_column("VALUE", overflow="fold")
    model_version_info = {
        "REGISTERED_MODEL_NAME": model_version.registered_model.name,
        "VERSION": model_version.version,
        "VERSION_DESCRIPTION": model_version.description,
        "CREATED_AT": str(model_version.created_at)
        if model_version.created_at
        else "N/A",
        "UPDATED_AT": str(model_version.last_updated_at)
        if model_version.last_updated_at
        else "N/A",
        "METADATA": model_version.metadata.dict()
        if model_version.metadata
        else {},
        "MODEL_SOURCE_URI": model_version.model_source_uri,
        "STAGE": model_version.stage.value,
    }

    for item in model_version_info.items():
        rich_table.add_row(*[str(elem) for elem in item])

    # capitalize entries in first column
    rich_table.columns[0]._cells = [
        component.upper()  # type: ignore[union-attr]
        for component in rich_table.columns[0]._cells
    ]
    console.print(rich_table)


def print_served_model_configuration(
    model_service: "BaseService", model_deployer: "BaseModelDeployer"
) -> None:
    """Prints the configuration of a model_service.

    Args:
        model_service: Specific service instance to
        model_deployer: Active model deployer
    """
    title_ = f"Properties of Served Model {model_service.uuid}"

    rich_table = table.Table(
        box=box.HEAVY_EDGE,
        title=title_,
        show_lines=True,
    )
    rich_table.add_column("MODEL SERVICE PROPERTY", overflow="fold")
    rich_table.add_column("VALUE", overflow="fold")

    # Get implementation specific info
    served_model_info = model_deployer.get_model_server_info(model_service)

    served_model_info = {
        **served_model_info,
        "UUID": str(model_service.uuid),
        "STATUS": get_service_state_emoji(model_service.status.state),
        "STATUS_MESSAGE": model_service.status.last_error,
        "PIPELINE_NAME": model_service.config.pipeline_name,
        "RUN_NAME": model_service.config.run_name,
        "PIPELINE_STEP_NAME": model_service.config.pipeline_step_name,
    }

    # Sort fields alphabetically
    sorted_items = {k: v for k, v in sorted(served_model_info.items())}

    for item in sorted_items.items():
        rich_table.add_row(*[str(elem) for elem in item])

    # capitalize entries in first column
    rich_table.columns[0]._cells = [
        component.upper()  # type: ignore[union-attr]
        for component in rich_table.columns[0]._cells
    ]
    console.print(rich_table)


def print_server_deployment_list(servers: List["ServerDeployment"]) -> None:
    """Print a table with a list of ZenML server deployments.

    Args:
        servers: list of ZenML server deployments
    """
    server_dicts = []
    for server in servers:
        status = ""
        url = ""
        connected = ""
        if server.status:
            status = get_service_state_emoji(server.status.status)
            if server.status.url:
                url = server.status.url
            if server.status.connected:
                connected = ":point_left:"
        server_dicts.append(
            {
                "STATUS": status,
                "NAME": server.config.name,
                "PROVIDER": server.config.provider.value,
                "URL": url,
                "CONNECTED": connected,
            }
        )
    print_table(server_dicts)


def print_server_deployment(server: "ServerDeployment") -> None:
    """Prints the configuration and status of a ZenML server deployment.

    Args:
        server: Server deployment to print
    """
    server_name = server.config.name
    title_ = f"ZenML server '{server_name}'"

    rich_table = table.Table(
        box=box.HEAVY_EDGE,
        title=title_,
        show_header=False,
        show_lines=True,
    )
    rich_table.add_column("", overflow="fold")
    rich_table.add_column("", overflow="fold")

    server_info = []

    if server.status:
        server_info.extend(
            [
                ("URL", server.status.url or ""),
                ("STATUS", get_service_state_emoji(server.status.status)),
                ("STATUS_MESSAGE", server.status.status_message or ""),
                (
                    "CONNECTED",
                    ":white_check_mark:" if server.status.connected else "",
                ),
            ]
        )

    for item in server_info:
        rich_table.add_row(*item)

    console.print(rich_table)


def describe_pydantic_object(schema_json: Dict[str, Any]) -> None:
    """Describes a Pydantic object based on the dict-representation of its schema.

    Args:
        schema_json: str, represents the schema of a Pydantic object, which
            can be obtained through BaseModelClass.schema_json()
    """
    # Get the schema dict
    # Extract values with defaults
    schema_title = schema_json["title"]
    required = schema_json.get("required", [])
    description = schema_json.get("description", "")
    properties = schema_json.get("properties", {})

    # Pretty print the schema
    warning(f"Configuration class: {schema_title}\n", bold=True)

    if description:
        declare(f"{description}\n")

    if properties:
        warning("Properties", bold=True)
        for prop, prop_schema in properties.items():

            if "$ref" not in prop_schema.keys():
                warning(
                    f"{prop}, {prop_schema['type']}"
                    f"{', REQUIRED' if prop in required else ''}"
                )

            if "description" in prop_schema:
                declare(f"  {prop_schema['description']}", width=80)


def get_shared_emoji(is_shared: bool) -> str:
    """Returns the emoji for whether a stack is shared or not.

    Args:
        is_shared: Whether the stack is shared or not.

    Returns:
        The emoji for whether the stack is shared or not.
    """
    return ":white_heavy_check_mark:" if is_shared else ":heavy_minus_sign:"


def print_stacks_table(
    client: "Client", stacks: Sequence["StackResponseModel"]
) -> None:
    """Print a prettified list of all stacks supplied to this method.

    Args:
        client: Repository instance
        stacks: List of stacks
    """
    stack_dicts = []
    active_stack_model_id = client.active_stack_model.id
    for stack in stacks:
        is_active = stack.id == active_stack_model_id

        if stack.user:
            user_name = stack.user.name
        else:
            user_name = "[DELETED]"

        stack_config = {
            "ACTIVE": ":point_right:" if is_active else "",
            "STACK NAME": stack.name,
            "STACK ID": stack.id,
            "SHARED": get_shared_emoji(stack.is_shared),
            "OWNER": user_name,
            **{
                component_type.upper(): components[0].name
                for component_type, components in stack.components.items()
            },
        }
        stack_dicts.append(stack_config)

    print_table(stack_dicts)


def print_components_table(
    client: "Client",
    component_type: StackComponentType,
    components: Sequence["ComponentResponseModel"],
) -> None:
    """Prints a table with configuration options for a list of stack components.

    If a component is active (its name matches the `active_component_name`),
    it will be highlighted in a separate table column.

    Args:
        client: Instance of the Repository singleton
        component_type: Type of stack component
        components: List of stack components to print.
    """
    display_name = _component_display_name(component_type, plural=True)
    if len(components) == 0:
        warning(f"No {display_name} registered.")
        return
    active_stack = client.active_stack_model
    active_component_id = None
    if component_type in active_stack.components.keys():
        active_components = active_stack.components[component_type]
        active_component_id = (
            active_components[0].id if active_components else None
        )

    configurations = []
    for component in components:
        is_active = component.id == active_component_id
        component_config = {
            "ACTIVE": ":point_right:" if is_active else "",
            "NAME": component.name,
            "COMPONENT ID": component.id,
            "FLAVOR": component.flavor,
            "SHARED": get_shared_emoji(component.is_shared),
            "OWNER": f"{component.user.name if component.user else 'DELETED!'}",
        }
        configurations.append(component_config)
    print_table(configurations)


def seconds_to_human_readable(time_seconds: int) -> str:
    """Converts seconds to human readable format.

    Args:
        time_seconds: Seconds to convert.

    Returns:
        Human readable string.
    """
    seconds = time_seconds % 60
    minutes = (time_seconds // 60) % 60
    hours = (time_seconds // 3600) % 24
    days = time_seconds // 86400
    tokens = []
    if days:
        tokens.append(f"{days}d")
    if hours:
        tokens.append(f"{hours}h")
    if minutes:
        tokens.append(f"{minutes}m")
    if seconds:
        tokens.append(f"{seconds}s")

    return "".join(tokens)


def expires_in(expires_at: datetime.datetime, expired_str: str) -> str:
    """Returns a human readable string of the time until the token expires.

    Args:
        expires_at: Datetime object of the token expiration.
        expired_str: String to return if the token is expired.

    Returns:
        Human readable string.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    if expires_at < now:
        return expired_str
    return seconds_to_human_readable((expires_at - now).seconds)


def print_service_connectors_table(
    client: "Client",
    connectors: Sequence["ServiceConnectorResponseModel"],
) -> None:
    """Prints a table with details for a list of service connectors.

    Args:
        client: Instance of the Repository singleton
        connectors: List of service connectors to print.
    """
    if len(connectors) == 0:
        return

    active_stack = client.active_stack_model
    active_connector_ids: List["UUID"] = []
    for components in active_stack.components.values():
        active_connector_ids.extend(
            [
                component.connector.id
                for component in components
                if component.connector
            ]
        )

    configurations = []
    for connector in connectors:

        is_active = connector.id in active_connector_ids
        labels = [
            f"{label}:{value}" for label, value in connector.labels.items()
        ]
        resource_name = connector.resource_id or "<multiple>"

        connector_config = {
            "ACTIVE": ":point_right:" if is_active else "",
            "NAME": connector.name,
            "ID": connector.id,
            "TYPE": connector.emojified_connector_type,
            "RESOURCE TYPES": "\n".join(connector.emojified_resource_types),
            "RESOURCE NAME": resource_name,
            "SHARED": get_shared_emoji(connector.is_shared),
            "OWNER": f"{connector.user.name if connector.user else 'DELETED!'}",
            "EXPIRES IN": expires_in(
                connector.expires_at, ":name_badge: Expired!"
            )
            if connector.expires_at
            else "",
            "LABELS": "\n".join(labels),
        }
        configurations.append(connector_config)
    print_table(configurations)


def print_service_connector_resource_table(
    resources: List["ServiceConnectorResourcesModel"],
) -> None:
    """Prints a table with details for a list of service connector resources.

    Args:
        resources: List of service connector resources to print.
    """
    resource_table = []
    for resource_model in resources:

        resource_types = resource_model.emojified_resource_types
        if not resource_model.resource_type:
            # Multi-type connector
            if resource_types:
                resource_type = "\n".join(resource_types)
            else:
                resource_type = "<multiple>"
            if resource_model.error:
                resource_ids = [f":collision: error: {resource_model.error}"]
            elif resource_model.resource_ids:
                resource_ids = resource_model.resource_ids
            else:
                resource_ids = [":person_shrugging: none listed"]
        else:
            # Single-type connector
            assert resource_types
            resource_type = resource_types[0]

            if resource_model.error:
                # Error fetching resources
                resource_ids = [f":collision: error: {resource_model.error}"]
            elif resource_model.resource_ids:
                resource_ids = resource_model.resource_ids
            else:
                resource_ids = [":person_shrugging: none listed"]

        resource_row: Dict[str, Any] = {
            "CONNECTOR ID": str(resource_model.id),
            "CONNECTOR NAME": resource_model.name,
            "CONNECTOR TYPE": resource_model.emojified_connector_type,
            "RESOURCE TYPE": resource_type,
            "RESOURCE NAMES": "\n".join(resource_ids),
        }
        resource_table.append(resource_row)
    print_table(resource_table)


def print_service_connector_configuration(
    connector: Union[
        "ServiceConnectorResponseModel", "ServiceConnectorRequestModel"
    ],
    active_status: bool,
    show_secrets: bool,
) -> None:
    """Prints the configuration options of a service connector.

    Args:
        connector: The service connector to print.
        active_status: Whether the connector is active.
        show_secrets: Whether to show secrets.
    """
    from uuid import UUID

    from zenml.models import ServiceConnectorResponseModel

    if connector.user:
        if isinstance(connector.user, UUID):
            user_name = str(connector.user)
        else:
            user_name = connector.user.name
    else:
        user_name = "[DELETED]"

    if isinstance(connector, ServiceConnectorResponseModel):
        declare(
            f"Service connector '{connector.name}' of type "
            f"'{connector.type}' with id '{connector.id}' is owned by "
            f"user '{user_name}' and is "
            f"'{'shared' if connector.is_shared else 'private'}'."
        )
    else:
        declare(
            f"Service connector '{connector.name}' of type "
            f"'{connector.type}' is "
            f"'{'shared' if connector.is_shared else 'private'}'."
        )

    title_ = (
        f"'{connector.name}' {connector.type} Service Connector " "Details"
    )

    if active_status:
        title_ += " (ACTIVE)"
    rich_table = table.Table(
        box=box.HEAVY_EDGE,
        title=title_,
        show_lines=True,
    )
    rich_table.add_column("PROPERTY")
    rich_table.add_column("VALUE", overflow="fold")

    if connector.expiration_seconds is None:
        expiration = "N/A"
    else:
        expiration = str(connector.expiration_seconds) + "s"

    if isinstance(connector, ServiceConnectorResponseModel):
        properties = {
            "ID": connector.id,
            "NAME": connector.name,
            "TYPE": connector.emojified_connector_type,
            "AUTH METHOD": connector.auth_method,
            "RESOURCE TYPES": ", ".join(connector.emojified_resource_types),
            "RESOURCE NAME": connector.resource_id or "<multiple>",
            "SECRET ID": connector.secret_id or "",
            "SESSION DURATION": expiration,
            "EXPIRES IN": expires_in(
                connector.expires_at, ":name_badge: Expired!"
            )
            if connector.expires_at
            else "N/A",
            "OWNER": user_name,
            "WORKSPACE": connector.workspace.name,
            "SHARED": get_shared_emoji(connector.is_shared),
            "CREATED_AT": connector.created,
            "UPDATED_AT": connector.updated,
        }
    else:
        properties = {
            "NAME": connector.name,
            "TYPE": connector.emojified_connector_type,
            "AUTH METHOD": connector.auth_method,
            "RESOURCE TYPES": ", ".join(connector.emojified_resource_types),
            "RESOURCE NAME": connector.resource_id or "<multiple",
            "SESSION DURATION": expiration,
            "EXPIRES IN": expires_in(
                connector.expires_at, ":name_badge: Expired!"
            )
            if connector.expires_at
            else "N/A",
            "SHARED": get_shared_emoji(connector.is_shared),
        }

    for item in properties.items():
        elements = [str(elem) for elem in item]
        rich_table.add_row(*elements)

    console.print(rich_table)

    if len(connector.configuration) == 0 and len(connector.secrets) == 0:
        declare("No configuration options are set for this connector.")

    else:
        rich_table = table.Table(
            box=box.HEAVY_EDGE,
            title="Configuration",
            show_lines=True,
        )
        rich_table.add_column("PROPERTY")
        rich_table.add_column("VALUE", overflow="fold")

        config = connector.configuration.copy()
        secrets = connector.secrets.copy()
        for key, value in secrets.items():
            if not show_secrets:
                config[key] = "[HIDDEN]"
            elif value is None:
                config[key] = "[UNAVAILABLE]"
            else:
                config[key] = value.get_secret_value()

        for item in config.items():
            elements = [str(elem) for elem in item]
            rich_table.add_row(*elements)

        console.print(rich_table)

    if not connector.labels:
        declare("No labels are set for this service connector.")
        return

    rich_table = table.Table(
        box=box.HEAVY_EDGE,
        title="Labels",
        show_lines=True,
    )
    rich_table.add_column("LABEL")
    rich_table.add_column("VALUE", overflow="fold")

    items = connector.labels.items()
    for item in items:
        elements = [str(elem) for elem in item]
        rich_table.add_row(*elements)

    console.print(rich_table)


def print_service_connector_types_table(
    connector_types: Sequence["ServiceConnectorTypeModel"],
) -> None:
    """Prints a table with details for a list of service connectors types.

    Args:
        connector_types: List of service connector types to print.
    """
    if len(connector_types) == 0:
        warning("No service connector types found.")
        return

    configurations = []
    for connector_type in connector_types:
        supported_auth_methods = list(connector_type.auth_method_map.keys())

        connector_type_config = {
            "NAME": connector_type.name,
            "TYPE": connector_type.emojified_connector_type,
            "RESOURCE TYPES": "\n".join(
                connector_type.emojified_resource_types
            ),
            "AUTH METHODS": "\n".join(supported_auth_methods),
            "LOCAL": get_shared_emoji(connector_type.local),
            "REMOTE": get_shared_emoji(connector_type.remote),
        }
        configurations.append(connector_type_config)
    print_table(configurations)


def print_service_connector_resource_type(
    resource_type: "ResourceTypeModel",
    title: str = "",
    heading: str = "#",
    footer: str = "---",
    print: bool = True,
) -> str:
    """Prints details for a service connector resource type.

    Args:
        resource_type: Service connector resource type to print.
        title: Markdown title to use for the resource type details.
        heading: Markdown heading to use for the resource type title.
        footer: Markdown footer to use for the resource type description.
        print: Whether to print the resource type details to the console or
            just return the message as a string.

    Returns:
        The MarkDown resource type details as a string.
    """
    message = f"{title}\n" if title else ""
    emoji = (
        Emoji(resource_type.emoji.strip(":")) if resource_type.emoji else ""
    )
    message += (
        f"{heading} {emoji} {resource_type.name} "
        f"(resource type: {resource_type.resource_type})\n"
    )
    message += (
        f"**Authentication methods**: "
        f"{', '.join(resource_type.auth_methods)}\n\n"
    )
    message += (
        f"**Supports resource instances**: "
        f"{resource_type.supports_instances}\n\n"
    )
    message += f"{resource_type.description}\n"

    message += footer

    if print:
        console.print(Markdown(message), justify="left", width=80)

    return message


def print_service_connector_auth_method(
    auth_method: "AuthenticationMethodModel",
    title: str = "",
    heading: str = "#",
    footer: str = "---",
    print: bool = True,
) -> str:
    """Prints details for a service connector authentication method.

    Args:
        auth_method: Service connector authentication method to print.
        title: Markdown title to use for the authentication method details.
        heading: Markdown heading to use for the authentication method title.
        footer: Markdown footer to use for the authentication method description.
        print: Whether to print the authentication method details to the console
            or just return the message as a string.

    Returns:
        The MarkDown authentication method details as a string.
    """
    message = f"{title}\n" if title else ""
    emoji = Emoji("lock")
    message += (
        f"{heading} {emoji} {auth_method.name} "
        f"(auth method: {auth_method.auth_method})\n"
    )
    message += (
        f"**Supports issuing temporary credentials**: "
        f"{auth_method.supports_temporary_credentials()}\n\n"
    )
    message += f"{auth_method.description}\n"
    message += footer

    if print:
        console.print(Markdown(message), justify="left", width=80)

    return message


def print_service_connector_type(
    connector_type: "ServiceConnectorTypeModel",
    title: str = "",
    heading: str = "#",
    footer: str = "---",
    include_resource_types: bool = True,
    include_auth_methods: bool = True,
    print: bool = True,
) -> str:
    """Prints details for a service connector type.

    Args:
        connector_type: Service connector type to print.
        title: Markdown title to use for the service connector type details.
        heading: Markdown heading to use for the service connector type title.
        footer: Markdown footer to use for the service connector type
            description.
        include_resource_types: Whether to include the resource types for the
            service connector type.
        include_auth_methods: Whether to include the authentication methods for
            the service connector type.
        print: Whether to print the service connector type details to the
            console or just return the message as a string.

    Returns:
        The MarkDown service connector type details as a string.
    """
    message = f"{title}\n" if title else ""
    supported_auth_methods = list(connector_type.auth_method_map.keys())
    supported_resource_types = [
        f'{Emoji(r.emoji.strip(":"))} {r.resource_type}'
        if r.emoji
        else r.resource_type
        for r in connector_type.resource_types
    ]

    emoji = (
        Emoji(connector_type.emoji.strip(":")) if connector_type.emoji else ""
    )

    message += (
        f"{heading} {emoji} {connector_type.name} "
        f"(connector type: {connector_type.connector_type})\n"
    )
    message += (
        f"**Authentication methods**: "
        f"{', '.join(supported_auth_methods)}\n\n"
    )
    message += (
        "**Resource types**:\n\n- "
        + "\n- ".join(supported_resource_types)
        + "\n\n"
    )
    message += (
        f"**Supports auto-configuration**: "
        f"{connector_type.supports_auto_configuration}\n\n"
    )
    message += f"**Available locally**: {connector_type.local}\n\n"
    message += f"**Available remotely**: {connector_type.remote}\n\n"
    message += f"{connector_type.description}\n"

    if include_resource_types:
        for r in connector_type.resource_types:
            message += print_service_connector_resource_type(
                r,
                heading=heading + "#",
                footer="",
                print=False,
            )

    if include_auth_methods:
        for a in connector_type.auth_methods:
            message += print_service_connector_auth_method(
                a,
                heading=heading + "#",
                footer="",
                print=False,
            )

    message += footer

    if print:
        console.print(Markdown(message), justify="left", width=80)

    return message


def _get_stack_components(
    stack: "Stack",
) -> "List[StackComponent]":
    """Get a dict of all components in a stack.

    Args:
        stack: A stack

    Returns:
        A list of all components in a stack.
    """
    return list(stack.components.values())


def _scrub_secret(config: StackComponentConfig) -> Dict[str, Any]:
    """Remove secret values from a configuration.

    Args:
        config: configuration for a stack component

    Returns:
        A configuration with secret values removed.
    """
    config_dict = {}
    config_fields = dict(config.__class__.__fields__)
    for key, value in config_fields.items():
        if secret_utils.is_secret_field(value):
            config_dict[key] = "********"
        else:
            config_dict[key] = getattr(config, key)
    return config_dict


def print_debug_stack() -> None:
    """Print active stack and components for debugging purposes."""
    from zenml.client import Client

    client = Client()
    stack = client.get_stack()
    active_stack = client.active_stack
    components = _get_stack_components(active_stack)

    declare("\nCURRENT STACK\n", bold=True)
    console.print(f"Name: {stack.name}")
    console.print(f"ID: {str(stack.id)}")
    console.print(f"Shared: {'Yes' if stack.is_shared else 'No'}")
    if stack.user and stack.user.name and stack.user.id:  # mypy check
        console.print(f"User: {stack.user.name} / {str(stack.user.id)}")
    console.print(
        f"Workspace: {stack.workspace.name} / {str(stack.workspace.id)}"
    )

    for component in components:
        component_response = client.get_stack_component(
            name_id_or_prefix=component.id, component_type=component.type
        )
        declare(
            f"\n{component.type.value.upper()}: {component.name}\n", bold=True
        )
        console.print(f"Name: {component.name}")
        console.print(f"ID: {str(component.id)}")
        console.print(f"Type: {component.type.value}")
        console.print(f"Flavor: {component.flavor}")
        console.print(f"Configuration: {_scrub_secret(component.config)}")
        console.print(
            f"Shared: {'Yes' if component_response.is_shared else 'No'}"
        )
        if (
            component_response.user
            and component_response.user.name
            and component_response.user.id
        ):  # mypy check
            console.print(
                f"User: {component_response.user.name} / {str(component_response.user.id)}"
            )
        console.print(
            f"Workspace: {component_response.workspace.name} / {str(component_response.workspace.id)}"
        )


def _component_display_name(
    component_type: "StackComponentType", plural: bool = False
) -> str:
    """Human-readable name for a stack component.

    Args:
        component_type: Type of the component to get the display name for.
        plural: Whether the display name should be plural or not.

    Returns:
        A human-readable name for the given stack component type.
    """
    name = component_type.plural if plural else component_type.value
    return name.replace("_", " ")


def get_execution_status_emoji(status: "ExecutionStatus") -> str:
    """Returns an emoji representing the given execution status.

    Args:
        status: The execution status to get the emoji for.

    Returns:
        An emoji representing the given execution status.

    Raises:
        RuntimeError: If the given execution status is not supported.
    """
    from zenml.enums import ExecutionStatus

    if status == ExecutionStatus.FAILED:
        return ":x:"
    if status == ExecutionStatus.RUNNING:
        return ":gear:"
    if status == ExecutionStatus.COMPLETED:
        return ":white_check_mark:"
    if status == ExecutionStatus.CACHED:
        return ":package:"
    raise RuntimeError(f"Unknown status: {status}")


def print_pipeline_runs_table(
    pipeline_runs: Sequence["PipelineRunResponseModel"],
) -> None:
    """Print a prettified list of all pipeline runs supplied to this method.

    Args:
        pipeline_runs: List of pipeline runs
    """
    runs_dicts = []
    for pipeline_run in pipeline_runs:

        if pipeline_run.user:
            user_name = pipeline_run.user.name
        else:
            user_name = "[DELETED]"

        if pipeline_run.pipeline is None:
            pipeline_name = "unlisted"
        else:
            pipeline_name = pipeline_run.pipeline.name
        if pipeline_run.stack is None:
            stack_name = "[DELETED]"
        else:
            stack_name = pipeline_run.stack.name
        status = pipeline_run.status
        status_emoji = get_execution_status_emoji(status)
        run_dict = {
            "PIPELINE NAME": pipeline_name,
            "RUN NAME": pipeline_run.name,
            "RUN ID": pipeline_run.id,
            "STATUS": status_emoji,
            "STACK": stack_name,
            "OWNER": user_name,
        }
        runs_dicts.append(run_dict)
    print_table(runs_dicts)


def warn_unsupported_non_default_workspace() -> None:
    """Warning for unsupported non-default workspace."""
    from zenml.constants import (
        ENV_ZENML_DISABLE_WORKSPACE_WARNINGS,
        handle_bool_env_var,
    )

    disable_warnings = handle_bool_env_var(
        ENV_ZENML_DISABLE_WORKSPACE_WARNINGS, False
    )
    if not disable_warnings:
        warning(
            "Currently the concept of `workspace` is not supported "
            "within the Dashboard. The Project functionality will be "
            "completed in the coming weeks. For the time being it "
            "is recommended to stay within the `default` workspace."
        )


def print_page_info(page: Page[T]) -> None:
    """Print all page information showing the number of items and pages.

    Args:
        page: The page to print the information for.
    """
    declare(
        f"Page `({page.index}/{page.total_pages})`, `{page.total}` items "
        f"found for the applied filters."
    )


F = TypeVar("F", bound=Callable[..., None])


def create_filter_help_text(
    filter_model: Type[BaseFilterModel], field: str
) -> str:
    """Create the help text used in the click option help text.

    Args:
        filter_model: The filter model to use
        field: The field within that filter model

    Returns:
        The help text.
    """
    if filter_model.is_sort_by_field(field):
        return (
            "[STRING] Example: --sort_by='desc:name' to sort by name in "
            "descending order. "
        )
    if filter_model.is_datetime_field(field):
        return (
            f"[DATETIME] The following datetime format is supported: "
            f"'{FILTERING_DATETIME_FORMAT}'. Make sure to keep it in "
            f"quotation marks. "
            f"Example: --{field}="
            f"'{GenericFilterOps.GTE}:{FILTERING_DATETIME_FORMAT}' to "
            f"filter for everything created on or after the given date."
        )
    elif filter_model.is_uuid_field(field):
        return (
            f"[UUID] Example: --{field}='{GenericFilterOps.STARTSWITH}:ab53ca' "
            f"to filter for all UUIDs starting with that prefix."
        )
    elif filter_model.is_int_field(field):
        return (
            f"[INTEGER] Example: --{field}='{GenericFilterOps.GTE}:25' to "
            f"filter for all entities where this field has a value greater than "
            f"or equal to the value."
        )
    elif filter_model.is_bool_field(field):
        return (
            f"[BOOL] Example: --{field}='True' to "
            f"filter for all instances where this field is true."
        )
    elif filter_model.is_str_field(field):
        return (
            f"[STRING] Example: --{field}='{GenericFilterOps.CONTAINS}:example' "
            f"to filter everything that contains the query string somewhere in "
            f"its {field}."
        )
    else:
        return ""


def create_data_type_help_text(
    filter_model: Type[BaseFilterModel], field: str
) -> str:
    """Create a general help text for a fields datatype.

    Args:
        filter_model: The filter model to use
        field: The field within that filter model

    Returns:
        The help text.
    """
    if filter_model.is_datetime_field(field):
        return (
            f"[DATETIME] supported filter operators: "
            f"{[str(op) for op in NumericFilter.ALLOWED_OPS]}"
        )
    elif filter_model.is_uuid_field(field):
        return (
            f"[UUID] supported filter operators: "
            f"{[str(op) for op in UUIDFilter.ALLOWED_OPS]}"
        )
    elif filter_model.is_int_field(field):
        return (
            f"[INTEGER] supported filter operators: "
            f"{[str(op) for op in NumericFilter.ALLOWED_OPS]}"
        )
    elif filter_model.is_bool_field(field):
        return (
            f"[BOOL] supported filter operators: "
            f"{[str(op) for op in BoolFilter.ALLOWED_OPS]}"
        )
    elif filter_model.is_str_field(field):
        return (
            f"[STRING] supported filter operators: "
            f"{[str(op) for op in StrFilter.ALLOWED_OPS]}"
        )
    else:
        return f"{field}"


def list_options(filter_model: Type[BaseFilterModel]) -> Callable[[F], F]:
    """Create a decorator to generate the correct list of filter parameters.

    The Outer decorator (`list_options`) is responsible for creating the inner
    decorator. This is necessary so that the type of `FilterModel` can be passed
    in as a parameter.

    Based on the filter model, the inner decorator extracts all the click
    options that should be added to the decorated function (wrapper).

    Args:
        filter_model: The filter model based on which to decorate the function.

    Returns:
        The inner decorator.
    """

    def inner_decorator(func: F) -> F:

        options = []
        data_type_descriptors = set()
        for k, v in filter_model.__fields__.items():
            if k not in filter_model.CLI_EXCLUDE_FIELDS:
                options.append(
                    click.option(
                        f"--{k}",
                        type=str,
                        default=v.default,
                        required=False,
                        help=create_filter_help_text(filter_model, k),
                    )
                )
            if k not in filter_model.FILTER_EXCLUDE_FIELDS:
                data_type_descriptors.add(
                    create_data_type_help_text(filter_model, k)
                )

        def wrapper(function: F) -> F:
            for option in reversed(options):
                function = option(function)
            return function

        func.__doc__ = (
            f"{func.__doc__} By default all filters are "
            f"interpreted as a check for equality. However advanced "
            f"filter operators can be used to tune the filtering by "
            f"writing the operator and separating it from the "
            f"query parameter with a colon `:`, e.g. "
            f"--field='operator:query'."
        )

        if data_type_descriptors:
            joined_data_type_descriptors = "\n\n".join(data_type_descriptors)

            func.__doc__ = (
                f"{func.__doc__} \n\n"
                f"\b Each datatype supports a specific "
                f"set of filter operations, here are the relevant "
                f"ones for the parameters of this command: \n\n"
                f"{joined_data_type_descriptors}"
            )

        return wrapper(func)

    return inner_decorator


@contextlib.contextmanager
def temporary_active_stack(
    stack_name_or_id: Union["UUID", str, None] = None
) -> Iterator["Stack"]:
    """Contextmanager to temporarily activate a stack.

    Args:
        stack_name_or_id: The name or ID of the stack to activate. If not given,
            this contextmanager will not do anything.

    Yields:
        The active stack.
    """
    from zenml.client import Client

    try:
        if stack_name_or_id:
            old_stack_id = Client().active_stack_model.id
            Client().activate_stack(stack_name_or_id)
        else:
            old_stack_id = None
        yield Client().active_stack
    finally:
        if old_stack_id:
            Client().activate_stack(old_stack_id)


def get_package_information(
    package_names: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Get a dictionary of installed packages.

    Args:
        package_names: Specific package names to get the information for.

    Returns:
        A dictionary of the name:version for the package names passed in or
            all packages and their respective versions.
    """
    import pkg_resources

    if package_names:
        return {
            pkg.key: pkg.version
            for pkg in pkg_resources.working_set
            if pkg.key in package_names
        }

    return {pkg.key: pkg.version for pkg in pkg_resources.working_set}


def print_user_info(info: Dict[str, Any]) -> None:
    """Print user information to the terminal.

    Args:
        info: The information to print.
    """
    for key, value in info.items():
        if key in ["packages", "query_packages"] and not bool(value):
            continue

        declare(f"{key.upper()}: {value}")


def warn_deprecated_secrets_manager() -> None:
    """Warning for deprecating secrets managers."""
    warning(
        "Secrets managers are deprecated and will be removed in an upcoming "
        "release in favor of centralized secrets management. Please consider "
        "migrating all your secrets to the centralized secrets store by means "
        "of the `zenml secrets-manager secret migrate` CLI command. "
        "See the `zenml secret` CLI command and the "
        "https://docs.zenml.io/starter-guide/production-fundamentals/secrets-management "
        "documentation page for more information."
    )


def get_parsed_labels(
    labels: Optional[List[str]], allow_label_only: bool = False
) -> Dict[str, Optional[str]]:
    """Parse labels into a dictionary.

    Args:
        labels: The labels to parse.
        allow_label_only: Whether to allow labels without values.

    Returns:
        A dictionary of the metadata.

    Raises:
        ValueError: If the labels are not in the correct format.
    """
    if not labels:
        return {}

    metadata_dict = {}
    for m in labels:
        try:
            key, value = m.split("=")
        except ValueError:
            if not allow_label_only:
                raise ValueError(
                    "Labels must be in the format key=value"
                ) from None
            key = m
            value = None
        metadata_dict[key] = value

    return metadata_dict
