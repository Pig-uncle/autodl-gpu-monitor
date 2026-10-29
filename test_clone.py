import copy
import unittest
from unittest.mock import Mock, patch
import threading
import monitor as m

class CloneTests(unittest.TestCase):
 def setUp(self):
  self.c=copy.deepcopy(m.DEFAULTS);self.c.update(strategy='new_only',creation_method='clone',clone_source_uuid='source-123',clone_copy_data=True,expand_disk_gb=250)
  self.row={'uuid':'source-123','status':'shutdown','region_sign':'west-B','expand_data_disk_size':200*m.GIB,'private_image_uuid':'','reproduction_uuid':'','reproduction_id':0,'cg_application_uuid':''}
  self.snap={'image':{'image_type':'public','image_name':'base-image-test','chip_corp':'nvidia','cpu_arch':'x86'},'cg_application_info':{}}
  self.machine={'machine_id':'m1','region_sign':'west-B','chip_corp':'nvidia','cpu_arch':'x86','gpu_name':'RTX PRO 6000','gpu_idle_num':1,'max_data_disk_expand_size':500*m.GIB,'on_line':2,'binding_instance_num':1,'max_instance_num':10,'rent_mode':'','payg_price':7000}
 def api(self):
  a=Mock();a.instance.return_value=self.row;a.call.return_value=self.snap;return a
 def test_source_validation_and_clone_payload(self):
  source=m.clone_source(self.api(),self.c);p=m.clone_payload(self.c,'m1',1,source)
  self.assertEqual(p['instance_uuid'],'source-123');self.assertTrue(p['instance_info']['copy_data_disk_after_clone']);self.assertFalse(p['instance_info']['keep_src_user_service_address_after_clone']);self.assertEqual(p['price_info']['expand_data_disk'],250*m.GIB)
 def test_system_only_is_selectable(self):
  self.c['clone_copy_data']=False;p=m.clone_payload(self.c,'m1',1,m.clone_source(self.api(),self.c));self.assertFalse(p['instance_info']['copy_data_disk_after_clone'])
 def test_source_must_be_shutdown_and_never_stop_it(self):
  a=self.api();a.instance.return_value={**self.row,'status':'running'}
  with self.assertRaisesRegex(ValueError,'关机'):m.clone_source(a,self.c)
  a.start.assert_not_called()
 def test_region_and_expansion_enforced(self):
  self.c['region_sign_list']=['other']
  with self.assertRaisesRegex(ValueError,'地区'):m.clone_source(self.api(),self.c)
  self.c['region_sign_list']=[];self.c['expand_disk_gb']=100
  with self.assertRaisesRegex(ValueError,'200 GB'):m.clone_source(self.api(),self.c)
 def test_architecture_required(self):
  a=self.api();a.call.return_value={'image':{}}
  with self.assertRaisesRegex(ValueError,'架构'):m.clone_source(a,self.c)
 def test_invalid_clone_configuration(self):
  with self.assertRaises(ValueError):m.validate_config({**self.c,'clone_source_uuid':''})
  with self.assertRaises(ValueError):m.validate_config({**self.c,'clone_copy_data':'yes'})
 def test_changed_source_prevents_order(self):
  a=self.api();source=m.clone_source(a,self.c);a.instance.return_value={**self.row,'status':'running'}
  w=m.Monitor();w.log=Mock()
  with patch.object(m,'atomic_json') as journal:
   with self.assertRaisesRegex(ValueError,'关机'):w._submit_new(a,self.c,'m1',1,{},None,source)
   journal.assert_not_called()
 def test_clone_wait_reports_copy_status_without_claiming_complete(self):
  a=Mock();a.instance.return_value={'uuid':'new','status':'running','start_mode':'gpu','additional_info':{'copy_data_disk_after_clone_status':'Running'}}
  w=m.Monitor();w.log=Mock();w._wait_running(a,'new',self.c,{'creation_method':'clone','clone_copy_data':True})
  self.assertEqual(w.status['result']['clone_copy_status'],'Running');self.assertTrue(any('请在 AutoDL 控制台核对' in x.args[0] for x in w.log.call_args_list))
 def test_clone_submits_source_and_selected_disk_to_clone_endpoint(self):
  api=self.api();source=m.clone_source(api,self.c);worker=m.Monitor();worker.log=Mock()
  def call(method,path,payload=None,params=None):
   if path=='instance/snapshot':return self.snap
   if path=='order/instance/clone/payg':
    self.assertEqual(payload['instance_uuid'],'source-123')
    self.assertTrue(payload['instance_info']['copy_data_disk_after_clone'])
    return 'new-uuid'
   self.fail('Unexpected endpoint: '+path)
  api.call.side_effect=call
  with patch.object(m,'atomic_json') as journal, patch.object(worker,'_wait_running') as wait:
   result=worker._submit_new(api,self.c,'m1',1,{'creation_method':'clone','clone_copy_data':True},None,source)
  self.assertTrue(result);self.assertEqual(journal.call_count,2);self.assertEqual(wait.call_args.args[1],'new-uuid')
 def test_clone_success_state_requests_gpu_start_once(self):
  api=Mock();api.instance.side_effect=[{'uuid':'new','status':'created_clone_success'},{'uuid':'new','status':'running','start_mode':'gpu','additional_info':{}}]
  worker=m.Monitor();worker.log=Mock();worker.stop_event.wait=Mock()
  worker._wait_running(api,'new',self.c,{'creation_method':'clone','clone_copy_data':False})
  api.start.assert_called_once_with('new');self.assertEqual(worker.status['phase'],'success')
 def test_clone_filters_other_regions_and_incompatible_machines(self):
  api=self.api();worker=m.Monitor();worker.log=Mock()
  machines=[{**self.machine,'machine_id':'wrong-region','region_sign':'west-C'},
            {**self.machine,'machine_id':'wrong-chip','chip_corp':'other'},
            {**self.machine,'machine_id':'wrong-arch','cpu_arch':'arm'},self.machine]
  api.machines.return_value=machines
  def call(method,path,payload=None,params=None):
   if path=='instance/snapshot':return self.snap
   if path=='order/price/preview':
    self.assertEqual(payload['machine_id'],'m1')
    return {'data_disk_info':{'data_disk_deal_daily_price':1000}}
   self.fail('Unexpected endpoint: '+path)
  api.call.side_effect=call
  def submit(a,c,mid,count,order,race,source):
   self.assertEqual(mid,'m1');self.assertEqual(source['region'],'west-B');return True
  with patch.object(worker,'_submit_new',side_effect=submit) as sent:worker._buy_new(api,self.c)
  sent.assert_called_once();self.assertEqual(api.machines.call_args.args[2],['west-B'])
 def test_parallel_clone_rechecks_original_inside_lock(self):
  api=self.api();source=m.clone_source(api,self.c);self.c.update(strategy='parallel',instance_uuid='original-1')
  api.instance.side_effect=[{'uuid':'original-1','status':'running','start_mode':'gpu'}]
  worker=m.Monitor();worker.log=Mock();race={'lock':threading.Lock(),'done':threading.Event(),'owner':None}
  with patch.object(m,'atomic_json') as journal,patch.object(worker,'_wait_running') as wait:
   worker._submit_new(api,self.c,'m1',1,{},race,source)
  journal.assert_not_called();self.assertTrue(race['done'].is_set());self.assertEqual(wait.call_args.args[1],'original-1')
 def test_invalid_source_is_checked_before_parallel_power_on(self):
  self.c.update(strategy='parallel',instance_uuid='original-1')
  api=self.api();api.instance.return_value={**self.row,'status':'running'}
  worker=m.Monitor();worker.log=Mock()
  with patch.object(m,'AutoDL',return_value=api),patch.object(worker,'_run_parallel') as parallel:
   worker._run(self.c)
  parallel.assert_not_called();api.start.assert_not_called();self.assertEqual(worker.status['phase'],'error')
 def test_failed_clone_is_not_retried(self):
  a=Mock();a.instance.return_value={'uuid':'new','status':'created_clone_failed'}
  w=m.Monitor();w.log=Mock()
  with self.assertRaisesRegex(ValueError,'created_clone_failed'):w._wait_running(a,'new',self.c,{'creation_method':'clone','clone_copy_data':True})
  a.start.assert_not_called()

if __name__=='__main__':unittest.main()
