import request from './index'

// view: overview | fault_type | root_cause | subsystem | case
// focus: 焦点节点名称或 id（fault_type 视图传故障类型名，case 视图可传 run_id）
export const getGraph = (params) => request.get('/graph', { params })
export const getGraphOptions = (nodeType) => request.get('/graph/options', { params: { node_type: nodeType } })
