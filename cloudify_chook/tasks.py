import os
import re
import json
import time
import yaml
import shutil
import requests
import tempfile

from uuid import uuid4
from packaging import version

from cloudify import context
from cloudify import manager
from cloudify import ctx as CloudifyContext
from cloudify import exceptions as cfy_exc

from cloudify.decorators import workflow
from cloudify_rest_client.client import CloudifyClient
from cloudify_rest_client.executions import Execution

CREATE_DEP = 'create_deployment_environment'
DELETE_DEP = 'delete_deployment_environment'
INSTALL_WF = 'install'
UNINSTALL_WF = 'uninstall'


def get_dep_execution(client, deployment_id, workflow):
    executions = client.executions.list(deployment_id=deployment_id,
                                        workflow_id=workflow,
                                        sort='created_at',
                                        is_descending=True)

    if executions and len(executions) > 0:
        return executions[0]


def wait_for_execution(client, execution_id):
    finished = False
    while not finished:
        try:
            exec = client.executions.get(execution_id, _include=['status'])
            if exec.get('status') in Execution.END_STATES:
                finished = True
            else:
                time.sleep(5)
        except Exception as e:
            if 'not found' in str(e):
                finished = True


def check_if_plugin_exist(client, plugin_name):
    is_installed = False
    if plugin_name:
        plugins_list = client.plugins.list(_include=['package_name'])
        for plugin in plugins_list:
            if plugin.get('package_name') == plugin_name:
                is_installed = True
                break
    return is_installed


def get_cloudify_version(client):
    version = client.manager.get_version()['version']
    cloudify_version = re.findall('(\\d+.\\d+.\\d+)', version)[0]
    return cloudify_version


def download_blueprint(blueprint_url):
    tmp_path = tempfile.mkdtemp()
    downloaded_file = ''
    with requests.get(blueprint_url,
                      allow_redirects=True,
                      stream=True) as response:
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(
                suffix="yaml", dir=tmp_path, delete=False) \
                as source_temp:
            downloaded_file = source_temp.name
            for chunk in \
                    response.iter_content(chunk_size=None):
                source_temp.write(chunk)
    return downloaded_file


def generate_blueprint(cfy_ver, plugin_name):
    tmp_path = tempfile.mkdtemp()
    downloaded_file = ''
    dsl_version = \
        '1_4' if version.parse(cfy_ver) >= version.parse('6.4') else '1_3'
    blueprint_template = {
        'tosca_definitions_version': 'cloudify_dsl_{0}'.format(dsl_version),
        'imports': [
            'http://cloudify.co/spec/cloudify/{0}/types.yaml'.format(cfy_ver),
            'plugin:{0}'.format(plugin_name)
        ]
    }
    with tempfile.NamedTemporaryFile(
            suffix="yaml", dir=tmp_path, delete=False) \
            as source_temp:
        downloaded_file = source_temp.name
        with open(downloaded_file, 'w') as f:
            yaml.dump(blueprint_template, f, sort_keys=False)
    return downloaded_file


def get_field_value_recursive(logger, properties, path):
    if not path:
        return properties
    key = path[0]
    if isinstance(properties, list):
        try:
            return get_field_value_recursive(
                logger,
                properties[int(key)],
                path[1:]
            )
        except Exception as e:
            logger.debug("Can't filter by {}".format(repr(e)))
            return None
    elif isinstance(properties, dict):
        try:
            return get_field_value_recursive(
                logger,
                properties[key],
                path[1:]
            )
        except Exception as e:
            logger.debug("Can't filter by {}".format(repr(e)))
            return None
    else:
        return None


def _check_filter(ctx, filter_by, inputs):
    if isinstance(filter_by, list):
        for field_desc in filter_by:
            # check type of field_desc
            if not isinstance(field_desc, dict):
                ctx.logger.debug(
                    "Event skipped by wrong field description.")
                return False

            # check path
            field_path = field_desc.get('path')
            if not field_path:
                ctx.logger.debug("Event skipped by undefined key.")
                return False

            # possible values
            field_values = field_desc.get('values')
            if not field_values:
                ctx.logger.debug("Event skipped by undefined values.")
                return False

            # check that we have such values in properties
            value = get_field_value_recursive(
                ctx.logger, inputs, field_path)

            # skip events if not in subset
            if value not in field_values:
                ctx.logger.debug(
                    "Event with {value} skipped by {key}:{values} rule."
                    .format(
                        value=repr(value), key=repr(field_path),
                        values=repr(field_values)))
                return False
    else:
        ctx.logger.debug(
            "Filter skipped by incorrect type of rules list.")
        return False

    # everything looks good
    return True


# callback name from hooks config
@workflow
def plugin_invoker(*args, **kwargs):
    # get current context
    ctx = kwargs.get('ctx', CloudifyContext)
    if ctx.type != context.DEPLOYMENT:
        raise cfy_exc.NonRecoverableError(
            "Called with wrong context: {ctx_type}".format(
                ctx_type=ctx.type
            )
        )

    # check inputs
    if len(args):
        inputs = args[0]
    else:
        inputs = kwargs.get('inputs', {})

    blueprint_id = inputs.get('blueprint_id')
    deployment_id = inputs.get('deployment_id')
    event_type = inputs.get('event_type')
    # skip hook events for deployments that we generate
    if (blueprint_id and deployment_id and blueprint_id == deployment_id
            and deployment_id.startswith('hook-')) or not deployment_id:
        return

    # get client from current manager
    client_config = kwargs.get('client_config', {})
    if client_config:
        client = CloudifyClient(**client_config)
    else:
        # get client from current manager
        client = manager.get_rest_client()

    try:
        deployment = client.deployments.get(deployment_id=deployment_id)
        if not deployment:
            return
        hooks_config = client.secrets.get('hooks_config').get('value')
        # an array of dicts where the hooks configuration would be in this
        # form [{
        # "plugin_name": "...",
        # "workflow_for_run": "...",
        # "workflow_params": {...},
        # "event_type": "...",
        # "filter_by": [{...}],
        # "active": True/False,
        # }]
    except Exception as e:
        if 'not found' in str(e):
            return

    for hook_config in json.loads(hooks_config):
        filter_by = hook_config.get('filter_by', [])
        is_active = hook_config.get('active', False)
        workflow_name = hook_config.get('workflow_for_run', '')
        # skip inactive hooks or different event_type or invalid workflow setup
        if hook_config.get('event_type', '') != event_type or not is_active \
                or not workflow_name:
            continue
        # check the filter if defined
        if filter_by:
            inputs['deployment_inputs'] = deployment.get('inputs', {})
            inputs['deployment_outputs'] = deployment.get('outputs', {})
            inputs['deployment_capabilities'] = \
                deployment.get('capabilities', {})
            if not _check_filter(ctx=ctx, filter_by=filter_by,
                                 inputs=inputs):
                continue

        # check if plugin is provided so we would create a temp blueprint
        # to call a special workflow that leverage deployment_id as an input

        plugin_name = hook_config.get('plugin_name', '')
        workflow_params = hook_config.get('workflow_params', {})

        if plugin_name and check_if_plugin_exist(client, plugin_name):

            temp_id = "hook-{0}".format(uuid4())
            blueprint_path = generate_blueprint(
                get_cloudify_version(client), plugin_name)
            workflow_params['deployment_id'] = deployment_id
            try:
                client.blueprints.upload(blueprint_path, temp_id)
                client.deployments.create(temp_id, temp_id)
                exec_id = \
                    get_dep_execution(client, temp_id, CREATE_DEP).get('id')
                wait_for_execution(client, exec_id)
                exec_id = \
                    client.executions.start(temp_id, INSTALL_WF).get('id')
                wait_for_execution(client, exec_id)
                exec_id = client.executions.start(
                    deployment_id=temp_id, workflow_id=workflow_name,
                    parameters=workflow_params).get('id')
                wait_for_execution(client, exec_id)
                # log to main deployment hook workflow execution result
                events = client.events.get(execution_id=exec_id,
                                           include_logs=True)
                events_messages = []
                for item in events[0]:
                    events_messages.append(item["message"])
                # handle a case where the parent deployment was removed
                # while the hook workflow was still being executed
                # so the context logger would throw exception
                try:
                    ctx.logger.info('{0} wf execution events\n{1}'.format(
                        workflow_name, "\n".join(events_messages)))
                except Exception:
                    pass
                # end of log
                exec_id = \
                    client.executions.start(temp_id, UNINSTALL_WF).get('id')
                wait_for_execution(client, exec_id)
                client.deployments.delete(temp_id)
                exec_id = \
                    get_dep_execution(client, temp_id, DELETE_DEP).get('id')
                wait_for_execution(client, exec_id)
                client.blueprints.delete(temp_id)
            except Exception as e:
                ctx.logger.error('Something went wrong {0}'.format(str(e)))
            finally:
                shutil.rmtree(os.path.dirname(blueprint_path),
                              ignore_errors=True)

        elif not plugin_name:
            # since no plugin was provided so we will execute the workflow
            # on the deployment itself
            client.executions.start(deployment_id=deployment_id,
                                    workflow_id=workflow_name,
                                    **workflow_params)
