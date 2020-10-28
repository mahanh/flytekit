from __future__ import annotations

from typing import Dict, Any, List

from flytekit import logger
from flytekit.annotated import workflow
from flytekit.annotated.context_manager import FlyteContext
from flytekit.annotated.interface import (
    transform_inputs_to_parameters,
    Interface,
)
from flytekit.annotated.type_engine import TypeEngine
from flytekit.common.launch_plan import SdkLaunchPlan
from flytekit.models import common as _common_models
from flytekit.models import interface as _interface_models
from flytekit.models import launch_plan as _launch_plan_models
from flytekit.models import literals as _literal_models
from flytekit.models import schedule as _schedule_model
from flytekit.models.core import identifier as _identifier_model


class LaunchPlan(object):
    # The reason we cache is simply because users may get the default launch plan twice for a single Workflow. We
    # don't want to create two defaults, could be confusing.
    CACHE = {}

    @staticmethod
    def native_kwargs_to_literal_map(ctx: FlyteContext, native_interface: Interface,
                                     typed_interface: _interface_models.TypedInterface,
                                     **kwargs) -> _literal_models.LiteralMap:
        return _literal_models.LiteralMap(
            literals={
                k: TypeEngine.to_literal(ctx, v, python_type=native_interface.inputs.get(k),
                                         expected=typed_interface.inputs.get(k).type)
                for k, v in kwargs.items()
            }
        )

    @staticmethod
    def get_default_launch_plan(ctx: FlyteContext, workflow: workflow.Workflow) -> LaunchPlan:
        if workflow.name in LaunchPlan.CACHE:
            return LaunchPlan.CACHE[workflow.name]

        parameter_map = transform_inputs_to_parameters(ctx, workflow._native_interface)

        lp = LaunchPlan(name=workflow.name, workflow=workflow, parameters=parameter_map,
                        fixed_inputs=_literal_models.LiteralMap(literals={}))

        LaunchPlan.CACHE[workflow.name] = lp
        return lp

    # TODO: Fix this, we need to have two sets of maps for inputs, one for fixed inputs, and one for default inputs.
    #  cuz we have both of those in the launch plan spec.
    @staticmethod
    def create(name: str, workflow: workflow.Workflow, **kwargs) -> LaunchPlan:
        ctx = FlyteContext.current_context()
        parameters = transform_inputs_to_parameters(ctx, workflow._native_interface)
        fixed_inputs = LaunchPlan.native_kwargs_to_literal_map(ctx, workflow._native_interface, workflow.interface,
                                                               **kwargs)

        lp = LaunchPlan(name=name, workflow=workflow, parameters=parameters, fixed_inputs=fixed_inputs)
        # This is just a convenience - we'll need the fixed inputs LiteralMap for when serializing the Launch Plan out
        # to protobuf, but for local execution and such, why not save the original Python native values as well so
        # we don't have to reverse it back every time.
        lp._saved_inputs = kwargs

        # This will eventually hold the registerable launch plan
        lp._registerable_entity: SdkLaunchPlan = None

        if name in LaunchPlan.CACHE:
            logger.warning(f"Launch plan named {name} was already created! Make sure your names are unique.")
        LaunchPlan.CACHE[name] = lp
        return lp

    # TODO: Add schedule, notifications, labels, annotations, QoS, raw output data config
    def __init__(self, name: str, workflow: workflow.Workflow, parameters: _interface_models.ParameterMap,
                 fixed_inputs: _literal_models.LiteralMap,
                 schedule: _schedule_model.Schedule = None,
                 notifications: List[_common_models.Notification] = None,
                 labels: _common_models.Labels = None,
                 annotations: _common_models.Annotations = None,
                 raw_output_data_config: _common_models.RawOutputDataConfig = None):
        self._name = name
        self._workflow = workflow
        # Ensure fixed inputs are not in parameter map
        parameters = {k: v for k, v in parameters.parameters.items() if
                      k not in fixed_inputs.literals and v.default is not None}
        self._parameters = _interface_models.ParameterMap(parameters=parameters)
        self._fixed_inputs = fixed_inputs
        self._saved_inputs = {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def parameters(self) -> _interface_models.ParameterMap:
        return self._parameters

    @property
    def fixed_inputs(self) -> _literal_models.LiteralMap:
        return self._fixed_inputs

    @property
    def workflow(self) -> workflow.Workflow:
        return self._workflow

    @property
    def saved_inputs(self) -> Dict[str, Any]:
        # See note in create()
        # Since the callsite will typically update the dict returned, and since update updates in place, let's return
        # a copy.
        # TODO: What issues will there be when we start introducing custom classes as input types?
        return self._saved_inputs.copy()

    def __call__(self, *args, **kwargs):
        if len(args) > 0:
            raise AssertionError("Only Keyword Arguments are supported for launch plan executions")

        ctx = FlyteContext.current_context()
        if ctx.compilation_state is not None:
            # This would literally be a copy paste of the workflow one with the one line change
            inputs = self.saved_inputs
            inputs.update(kwargs)
            results = self.workflow._create_and_link_node(ctx, **inputs)
            node = ctx.compilation_state.nodes[-1]
            # Overwrite the flyte entity to be yourself instead.
            node._flyte_entity = self
            return results
        else:
            # Calling a launch plan should just forward the call to the workflow, nothing more. But let's add in the
            # saved inputs.
            inputs = self.saved_inputs
            inputs.update(kwargs)
            return self.workflow(*args, **inputs)

    def get_registerable_entity(self) -> SdkLaunchPlan:
        settings = FlyteContext.current_context().registration_settings
        if self._registerable_entity is not None:
            return self._registerable_entity

        if settings.iam_role:
            auth_role = _common_models.AuthRole(assumable_iam_role=settings.iam_role)
        elif settings.service_account:
            auth_role = _common_models.AuthRole(kubernetes_service_account=settings.service_account)
        else:
            auth_role = None

        sdk_workflow = self.workflow.get_registerable_entity()
        self._registerable_entity = SdkLaunchPlan(
            workflow_id=sdk_workflow.id,
            entity_metadata=_launch_plan_models.LaunchPlanMetadata(
                schedule=_schedule_model.Schedule(""), notifications=[],
            ),
            default_inputs=self.parameters,
            fixed_inputs=self.fixed_inputs,
            labels=_common_models.Labels({}),
            annotations=_common_models.Annotations({}),
            auth_role=auth_role,  # TODO: Is None here okay?
            raw_output_data_config=_common_models.RawOutputDataConfig(""),
        )
        self._registerable_entity._id = _identifier_model.Identifier(
            resource_type=_identifier_model.ResourceType.LAUNCH_PLAN, project=settings.project,
            domain=settings.domain,
            name=self.name,
            version=settings.version,
        )
        return self._registerable_entity