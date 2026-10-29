import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import requests
import monitor as m

class BillingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.dir=Path(self.temp.name);self.patches=[patch.object(m,'JOURNAL',self.dir/'order.json'),patch.object(m,'LOCAL',self.dir),patch.object(m,'load_authorization',return_value='')]
  for p in self.patches:p.start()
  self.c=copy.deepcopy(m.DEFAULTS);self.c.update(strategy='new_only',billing_type='daily',billing_duration=2,max_order_price=50)
  self.detail={'order':{'uuid':'order-123','status':'unpaid','machine_id':'m1','charge_type':'daily','order_type':'create_instance','deal_price':45000,'price_info':{'num':1,'duration':2,'expand_data_disk':250*m.GIB}}}
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def worker(self):
  w=m.Monitor();w.log=Mock();w._wait_running=Mock();return w
 def api(self):
  a=Mock();a.call.side_effect=[{'uuid':'order-123'},self.detail,'new-instance'];return a
 def test_all_billing_types_and_duration_bounds(self):
  for kind,maximum in [('payg',1),('daily',6),('weekly',3),('monthly',12)]:
   m.validate_config({**self.c,'billing_type':kind,'billing_duration':maximum})
   with self.assertRaises(ValueError):m.validate_config({**self.c,'billing_type':kind,'billing_duration':maximum+1})
 def test_prepay_requires_type_and_sufficient_machine_deadline(self):
  from datetime import datetime,timedelta,timezone
  machine={'pay_daily':True,'rent_deadline':(datetime.now(timezone.utc)+timedelta(days=3)).isoformat()}
  self.assertTrue(m.billing_eligible(machine,self.c))
  self.assertFalse(m.billing_eligible({**machine,'pay_daily':False},self.c))
  self.assertFalse(m.billing_eligible({**machine,'rent_deadline':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()},self.c))
  self.assertFalse(m.billing_eligible({**machine,'rent_deadline':'invalid'},self.c))
 def test_prepaid_creation_and_payment_follow_distinct_api_steps(self):
  a=self.api();w=self.worker();self.assertTrue(w._submit_new(a,self.c,'m1',1,{'creation_method':'fresh'},None))
  self.assertEqual([(x.args[0],x.args[1]) for x in a.call.call_args_list],[('POST','order/instance/create/prepay'),('GET','order'),('PUT','order/pay')])
  order=json.loads(m.JOURNAL.read_text());self.assertEqual(order['order_uuid'],'order-123');self.assertEqual(order['uuid'],'new-instance');self.assertEqual(order['order_price'],45);self.assertEqual(w._wait_running.call_args.args[1],'new-instance')
 def test_clone_prepaid_uses_clone_endpoint(self):
  a=self.api();a.call.side_effect=[{'uuid':'order-123'},{'order':{**self.detail['order'],'order_type':'clone_instance'}},'new-instance']
  c={**self.c,'creation_method':'clone','clone_source_uuid':'source-1'}
  source={'uuid':'source-1','base_image_uuid':'base','private_image_uuid':'','reproduction_uuid':'','reproduction_id':0,'cg_application_uuid':'','cg_application_info':{}}
  w=self.worker()
  with patch.object(m,'clone_source',return_value=source):w._submit_new(a,c,'m1',1,{'creation_method':'clone'},None,source)
  self.assertEqual(a.call.call_args_list[0].args[1],'order/instance/clone/prepay')
 def test_prepay_search_uses_selected_type_and_order_budget(self):
  from datetime import datetime,timedelta,timezone
  machine={'machine_id':'m1','gpu_name':'RTX PRO 6000','gpu_idle_num':1,'max_data_disk_expand_size':500*m.GIB,'on_line':2,'binding_instance_num':1,'max_instance_num':10,'rent_mode':'','payg_price':7000,'pay_daily':True,'rent_deadline':(datetime.now(timezone.utc)+timedelta(days=3)).isoformat()}
  a=Mock();a.machines.return_value=[machine];a.call.return_value={'deal_price':45000,'prepay_expand_data_disk':False,'data_disk_info':{'data_disk_deal_daily_price':1000}}
  w=self.worker()
  with patch.object(w,'_submit_new',return_value=True) as submit:w._buy_new(a,self.c)
  self.assertEqual(a.machines.call_args.args[4],'daily');self.assertEqual(a.call.call_args.args[2]['charge_type'],'daily');self.assertEqual(a.call.call_args.args[2]['duration'],2)
  self.assertEqual(submit.call_args.args[4]['quoted_order_price'],45)
 def test_prepay_quote_above_budget_never_creates_order(self):
  from datetime import datetime,timedelta,timezone
  machine={'machine_id':'m1','gpu_name':'RTX PRO 6000','gpu_idle_num':1,'max_data_disk_expand_size':500*m.GIB,'on_line':2,'binding_instance_num':1,'max_instance_num':10,'rent_mode':'','payg_price':7000,'pay_daily':True,'rent_deadline':(datetime.now(timezone.utc)+timedelta(days=3)).isoformat()}
  a=Mock();a.machines.return_value=[machine];w=self.worker()
  def preview(*args,**kwargs):w.stop_event.set();return {'deal_price':51000,'prepay_expand_data_disk':True}
  a.call.side_effect=preview
  with patch.object(w,'_submit_new') as submit:w._buy_new(a,self.c)
  submit.assert_not_called();self.assertFalse(m.JOURNAL.exists())
 def test_unknown_extra_disk_billing_fails_closed(self):
  from datetime import datetime,timedelta,timezone
  machine={'machine_id':'m1','gpu_name':'RTX PRO 6000','gpu_idle_num':1,'max_data_disk_expand_size':500*m.GIB,'on_line':2,'binding_instance_num':1,'max_instance_num':10,'rent_mode':'','payg_price':7000,'pay_daily':True,'rent_deadline':(datetime.now(timezone.utc)+timedelta(days=3)).isoformat()}
  a=Mock();a.machines.return_value=[machine];a.call.return_value={'deal_price':45000};w=self.worker()
  with patch.object(w,'_submit_new') as submit,self.assertRaisesRegex(ValueError,'计费方式'):w._buy_new(a,self.c)
  submit.assert_not_called()
 def test_actual_order_over_budget_blocks_payment(self):
  a=self.api();a.call.side_effect=[{'uuid':'order-123'},{'order':{**self.detail['order'],'deal_price':51000}}]
  with self.assertRaisesRegex(ValueError,'超过上限'):self.worker()._submit_new(a,self.c,'m1',1,{},None)
  self.assertEqual(a.call.call_count,2);self.assertEqual(json.loads(m.JOURNAL.read_text())['state'],'order_created')
 def test_mismatched_order_blocks_payment(self):
  for change in [{'status':'paid'},{'machine_id':'wrong'},{'order_type':'clone_instance'},{'charge_type':'monthly'}]:
   a=self.api();a.call.side_effect=[{'uuid':'order-123'},{'order':{**self.detail['order'],**change}}]
   with self.subTest(change=change),self.assertRaisesRegex(ValueError,'无法核实'):self.worker()._submit_new(a,self.c,'m1',1,{},None)
   self.assertEqual(a.call.call_count,2)
 def test_payment_timeout_is_not_retried_after_restart(self):
  a=self.api();a.call.side_effect=[{'uuid':'order-123'},self.detail,requests.Timeout()]
  with self.assertRaisesRegex(ValueError,'不会自动重复支付'):self.worker()._submit_new(a,self.c,'m1',1,{},None)
  self.assertEqual(json.loads(m.JOURNAL.read_text())['state'],'paying')
  b=Mock();w=self.worker()
  with patch.object(m,'AutoDL',return_value=b):w._run(self.c)
  self.assertEqual(w.status['phase'],'error');b.call.assert_not_called();b.machines.assert_not_called()
 def test_unexpected_creation_response_keeps_record(self):
  a=self.api();a.call.side_effect=[{}]
  with self.assertRaisesRegex(ValueError,'缺少订单号'):self.worker()._submit_new(a,self.c,'m1',1,{'state':'submitting'},None)
  self.assertEqual(json.loads(m.JOURNAL.read_text())['state'],'submitting')
 def test_stop_before_payment_preserves_order(self):
  a=self.api();w=self.worker()
  def call(method,path,payload=None,params=None):
   if path.endswith('prepay'):w.stop_event.set();return {'uuid':'order-123'}
   self.fail('Stop should prevent additional requests')
  a.call.side_effect=call
  self.assertTrue(w._submit_new(a,self.c,'m1',1,{},None));self.assertEqual(a.call.call_count,1);self.assertEqual(json.loads(m.JOURNAL.read_text())['state'],'order_created')
 def test_payg_payload_unchanged(self):
  c={**self.c,'billing_type':'payg','billing_duration':1};p=m.create_payload(c,'m1',2)
  self.assertEqual(p['instance_info']['charge_type'],'payg');self.assertEqual(p['price_info']['duration'],1)

if __name__=='__main__':unittest.main()
