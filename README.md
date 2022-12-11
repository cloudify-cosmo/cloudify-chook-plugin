Cloudify Custom Hook Plugin
========================

Cloudify plugin that you can use to customize hooks from a stored secret value.

This plugin handle two cases:

1. call a workflow on the deployment itself.
2. trigger a workflow from an another plugin via a temp blueprint [if plugin_name is provided and it is installed].

The flow is the following , you upload this plugin to the manager and modify `/opt/mgmtworker/config/hooks.conf`

```yaml
hooks:
  - event_type: workflow_started
    implementation: cloudify-chook-plugin.cloudify_chook.tasks.plugin_invoker
    inputs: ~
    description: A generic hook plugin that invokes another workflows based on secrets setup.
  - event_type: workflow_succeeded
    implementation: cloudify-chook-plugin.cloudify_chook.tasks.plugin_invoker
    inputs: ~
    description: A generic hook plugin that invokes another workflows based on secrets setup.
  - event_type: workflow_failed
    implementation: cloudify-chook-plugin.cloudify_chook.tasks.plugin_invoker
    inputs: ~
    description: A generic hook plugin that invokes another workflows based on secrets setup.
  - event_type: workflow_cancelled
    implementation: cloudify-chook-plugin.cloudify_chook.tasks.plugin_invoker
    inputs: ~
    description: A generic hook plugin that invokes another workflows based on secrets setup.
```

then restart mgmtworker service : `supervisorctl restart cloudify-mgmtworker`.

secret to be configured inside the tenant secret store called `hooks_config` and the structure of this secret is an array of dicts

```json
[{
    "plugin_name": "",
    "workflow_for_run": "",
    "workflow_params": {
      ....
    },
    "event_type": "",
    "filter_by": [{
      ...
    }],
    "active": true/false
}]
```
