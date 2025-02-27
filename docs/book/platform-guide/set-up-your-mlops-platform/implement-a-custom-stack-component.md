---
description: How to write a custom stack component flavor
---

# Implement a custom stack component

When building a sophisticated MLOps Platform, you will often need to come up with custom-tailored solutions. Sometimes, this might even require you to use custom components for your infrastructure or tooling.

That is exactly why the stack component flavors in ZenML are designed to be modular and straightforward to extend. Using ZenML's base abstractions, you can create your own stack component flavor and use it in your stack.

## Base Abstractions

Before we get into the topic of creating custom stack component flavors, let us briefly discuss some important design choices behind the abstraction of a ZenML flavor. The overall implementation revolves around three base interfaces, namely the `StackComponent`, the `StackComponentConfig`, and the `Flavor`.

### Base Abstraction 1: `StackComponent`

The `StackComponent` is utilized as an interface to define the logic behind the functionality of a flavor. For instance, you can take a look at the `BaseArtifactStore` example down below. By inheriting from the `StackComponent`, the `BaseArtifactStore` establishes the interface for all artifact stores. Any flavor of an artifact store needs to follow the standards set by this base class.

```python
class StackComponent:
    """Abstract StackComponent class for all components of a ZenML stack."""


class BaseArtifactStore(StackComponent):
    """Base class for all ZenML artifact stores."""

    # --- public interface ---

    @abstractmethod
    def open(self, name: PathType, mode: str = "r") -> Any:
        """Open a file at the given path."""

    @abstractmethod
    def exists(self, path: PathType) -> bool:
        """Checks if a path exists."""

    ...
```

### Base Abstraction 2: `StackComponentConfig`

As its name suggests, the `StackComponentConfig` is used to configure a stack component instance. It is separated from the actual implementation on purpose. This way, ZenML can use this class to validate the configuration of a stack component during its registration/update, without having to import heavy (or even non-installed) dependencies. Let us continue with the same example up above and take a look at the `BaseArtifactStoreConfig`.

```python
from pydantic import BaseModel


class StackComponentConfig(BaseModel):
    """Base class for all ZenML stack component configs."""


class BaseArtifactStoreConfig(StackComponentConfig):
    """Config class for `BaseArtifactStore`."""

    path: str

    SUPPORTED_SCHEMES: ClassVar[Set[str]]

    @root_validator(skip_on_failure=True)
    def _ensure_artifact_store(cls, values: Dict[str, Any]) -> Any:
        """Validator function for the Artifact Stores.

        Checks whether supported schemes are defined and the given path is
        supported.
        """
        ...
```

There are a few things to unpack here. Let's talk about Pydantic first. Pydantic is a library for [data validation and settings management](https://pydantic-docs.helpmanual.io/). By using their `BaseModel` as a base class, ZenML is able to configure and serialize these configuration properties while being able to add a validation layer to each implementation.

If you take a closer look at the example above, you will see that, through the `BaseArtifactStoreConfig`, each artifact store will require users to define a `path` variable along with a list of `SUPPORTED_SCHEMES`. Using this configuration class, ZenML can check if the given `path` is actually supported.

{% hint style="info" %}
Similar to the example above, you can use class variables by denoting them with the `ClassVar[..]`, which are also excluded from the serialization.
{% endhint %}

### Base Abstraction 3: `Flavor`

Ultimately, the `Flavor` abstraction is responsible for bringing the implementation of a `StackComponent` together with the corresponding `StackComponentConfig` definition to create a `Flavor`.

```python
class Flavor:
    """Base class for ZenML Flavors."""

    @property
    @abstractmethod
    def name(self) -> str:
        """The name of the flavor."""

    @property
    @abstractmethod
    def type(self) -> StackComponentType:
        """The type of the flavor."""

    @property
    @abstractmethod
    def implementation_class(self) -> Type[StackComponent]:
        """Implementation class for this flavor."""

    @property
    @abstractmethod
    def config_class(self) -> Type[StackComponentConfig]:
        """Configuration class for this flavor."""


class BaseArtifactStoreFlavor(Flavor):
    """Base class for artifact store flavors."""

    @property
    def type(self) -> StackComponentType:
        """Returns the flavor type."""
        return StackComponentType.ARTIFACT_STORE

    @property
    def config_class(self) -> Type[StackComponentConfig]:
        """Config class for this flavor."""
        return BaseArtifactStoreConfig
```

Following the same example, the `BaseArtifactStoreFlavor` sets the correct `type` property and introduces the `BaseArtifactStoreConfig` as the default configuration for all ZenML artifact stores.

## Implementing a Custom Stack Component Flavor

Using all the abstraction layers above, let us create a custom artifact store flavor, starting with the configuration.

```python
from zenml.artifact_stores import BaseArtifactStoreConfig


class MyArtifactStoreConfig(BaseArtifactStoreConfig):
    """Custom artifact store implementation."""

    my_param: int  # Adding a custom parameter on top of the `path` variable
```

With the configuration defined, we can move on to the logic behind the implementation:

```python
PathType = Union[bytes, str]

from zenml.artifact_stores import BaseArtifactStore


class MyArtifactStore(BaseArtifactStore):
    """Custom artifact store implementation."""

    def open(self, name: PathType, mode: str = "r") -> Any:
        """Custom logic goes here."""
        ...

    def exists(self, path: PathType) -> bool:
        """Custom logic goes here."""
        ...

    def my_custom_method(self):
        """Custom method here."""
        print(self.config.path)  # The configuration properties are available 
        print(self.config.my_param)  # under self.config
```

Now, let us bring these two classes together through a `Flavor`. Make sure that you give your flavor a unique name here.

```python
from zenml.artifact_stores import BaseArtifactStoreFlavor


class MyArtifactStoreFlavor(BaseArtifactStoreFlavor):
    """Custom artifact store implementation."""

    @property
    def name(self) -> str:
        """The name of the flavor."""
        return 'my_artifact_store'

    @property
    def implementation_class(self) -> Type["BaseArtifactStore"]:
        """Implementation class for this flavor."""
        from ... import MyArtifactStore
        return MyArtifactStore

    @property
    def config_class(self) -> Type[StackComponentConfig]:
        """Configuration class for this flavor."""
        from ... import MyArtifactStoreConfig
        return MyArtifactStoreConfig
```

## Managing a Custom Stack Component Flavor

Once your implementation is complete, you can register it through the CLI. Please ensure you **point to the flavor class via dot notation**:

```shell
zenml artifact-store flavor register <path.to.MyArtifactStoreFlavor>
```

For example, if your flavor class `MyArtifactStoreFlavor` is defined in `flavors/my_flavor.py`, you'd register it by doing:

```shell
zenml artifact-store flavor register flavors.my_flavor.MyArtifactStoreFlavor
```

{% hint style="warning" %}
ZenML resolves the flavor class by taking the path where you initialized ZenML (via `zenml init`) as the starting point of resolution. Therefore, please ensure you follow [the best practice](../../user-guide/starter-guide/follow-best-practices.md) of initializing ZenML at the root of your repository.

If ZenML does not find an initialized ZenML repository in any parent directory, it will default to the current working directory, but usually, it's better to not have to rely on this mechanism and initialize ZenML at the root.
{% endhint %}

Afterward, you should see the new custom artifact store flavor in the list of available artifact store flavors:

```shell
zenml artifact-store flavor list
```

And that's it, you now have defined a custom stack component flavor that you can use in any of your stacks just like any other flavor you used before, e.g.:

```shell
zenml artifact-store register <ARTIFACT_STORE_NAME> \
    --flavor=my_artifact_store \
    ...

zenml stack register <STACK_NAME> \
    --artifact-store <ARTIFACT_STORE_NAME> \
    --path='some-path' \
    --my_param=3 
```

{% hint style="info" %}
If you would like to automatically track some metadata about your custom stack component with each pipeline run, check out the [Tracking Custom Stack Component Metadata](../../user-guide/advanced-guide/pipelining-features/fetch-metadata-within-steps.md) section.
{% endhint %}

Check out [this short (< 3 minutes) video](https://www.youtube.com/watch?v=CQRVSKbBjtQ) on how to quickly get some more information about the registered flavors available to you:

{% embed url="https://www.youtube.com/watch?v=CQRVSKbBjtQ" %}
Describe MLOps Stack Component Flavors
{% endembed %}

## Extending Specific Stack Components

If you would like to learn more about how to build a custom stack component flavor for a specific stack component, please check the links below:

| **Type of Stack Component**                                                           | **Description**                                                   |
|---------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| [Orchestrator](../../user-guide/component-guide/orchestrators/custom.md)              | Orchestrating the runs of your pipeline                           |
| [Artifact Store](../../user-guide/component-guide/artifact-stores/custom.md)          | Storage for the artifacts created by your pipelines               |
| [Container Registry](../../user-guide/component-guide/container-registries/custom.md) | Store for your containers                                         |
| [Step Operator](../../user-guide/component-guide/step-operators/custom.md)            | Execution of individual steps in specialized runtime environments |
| [Model Deployer](../../user-guide/component-guide/model-deployers/custom.md)          | Services/platforms responsible for online model serving           |
| [Feature Store](../../user-guide/component-guide/feature-stores/custom.md)            | Management of your data/features                                  |
| [Experiment Tracker](../../user-guide/component-guide/experiment-trackers/custom.md)  | Tracking your ML experiments                                      |
| [Alerter](../../user-guide/component-guide/alerters/custom.md)                        | Sending alerts through specified channels                         |
| [Annotator](../../user-guide/component-guide/annotators/custom.md)                    | Annotating and labeling data                                      |
| [Data Validator](../../user-guide/component-guide/data-validators/custom.md)          | Validating and monitoring your data                               |

<!-- For scarf -->
<figure><img alt="ZenML Scarf" referrerpolicy="no-referrer-when-downgrade" src="https://static.scarf.sh/a.png?x-pxid=f0b4f458-0a54-4fcd-aa95-d5ee424815bc" /></figure>
