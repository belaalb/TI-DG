import argparse
import os
import sys
import random
import torch.optim as optim
import torch.utils.data
from torchvision import datasets
from torchvision import transforms
import argparse
import os
import sys
import random
from tqdm import tqdm
import PIL
import pandas
import models as models
from train_loop_2sources import TrainLoop
from data_loader_2sources import Loader_validation, Loader_unif_sampling
import utils

parser = argparse.ArgumentParser(description='RP for domain generalization')
parser.add_argument('--batch-size', type=int, default=64, metavar='N', help='input batch size for training (default: 64)')
parser.add_argument('--epochs', type=int, default=100, metavar='N', help='number of epochs to train (default: 50)')
parser.add_argument('--lr-task', type=float, default=0.01, metavar='LR', help='learning rate (default: 0.0002)')
parser.add_argument('--lr-domain', type=float, default=0.01, metavar='LR', help='learning rate (default: 0.0002)')
parser.add_argument('--lr-threshold', type=float, default=1e-4, metavar='LRthrs', help='learning rate (default: 1e-4)')
parser.add_argument('--momentum-task', type=float, default=0.9, metavar='m', help='momentum (default: 0.9)')
parser.add_argument('--momentum-domain', type=float, default=0.9, metavar='m', help='momentum (default: 0.9)')
parser.add_argument('--l2', type=float, default=1e-5, metavar='L2', help='Weight decay coefficient (default: 0.00001')
parser.add_argument('--factor', type=float, default=0.1, metavar='f', help='LR decrease factor (default: 0.1')
parser.add_argument('--checkpoint-epoch', type=int, default=None, metavar='N', help='epoch to load for checkpointing. If None, training starts from scratch')
parser.add_argument('--checkpoint-path', type=str, default='./', metavar='Path', help='Path for checkpointing')
parser.add_argument('--data-path', type=str, default='../data/vlcs/prepared_data/', metavar='Path', help='Data path')
parser.add_argument('--source1', type=str, default='CALTECH', metavar='Path', help='Path to source1 file')
parser.add_argument('--source2', type=str, default='LABELME', metavar='Path', help='Path to source2 file')
parser.add_argument('--target1', type=str, default='SUN', metavar='Path', help='Path to target1 file')
parser.add_argument('--target2', type=str, default='PASCAL', metavar='Path', help='Path to target2 data')
parser.add_argument('--seed', type=int, default=1, metavar='S', help='random seed (default: 1)')
parser.add_argument('--nadir-slack', type=float, default=1.5, metavar='nadir', help='factor for nadir-point update. Only used in hyper mode (default: 1.5)')
parser.add_argument('--alpha', type=float, default=0.8, metavar='alpha', help='balance losses to train encoder. Should be within [0,1]')
parser.add_argument('--rp-size', type=int, default=3000, metavar='rp', help='Random projection size. Should be smaller than 4096')
parser.add_argument('--patience', type=int, default=20, metavar='N', help='number of epochs to wait before reducing lr (default: 20)')
parser.add_argument('--smoothing', type=float, default=0.2, metavar='l', help='Label smoothing (default: 0.2)')
parser.add_argument('--warmup-its', type=float, default=500, metavar='w', help='LR warm-up iterations (default: 500)')
parser.add_argument('--workers', type=int, help='number of data loading workers', default=4)
parser.add_argument('--save-every', type=int, default=5, metavar='N', help='how many epochs to wait before logging training status. Default is 5')
parser.add_argument('--no-cuda', action='store_true', default=False, help='Disables GPU use')
parser.add_argument('--no-logging', action='store_true', default=False, help='Deactivates logging')
parser.add_argument('--ablation', choices = ['all', 'RP', 'no'], default='no', help='Ablation study (removing only RPs (option: RP), RPs+domain classifier (option: all), (default: no))')
parser.add_argument('--train-mode', choices = ['hv', 'avg'], default='avg', help='Train mode (options: hv, avg), (default: hv))')
parser.add_argument('--n-runs', type=int, default=1, metavar='n', help='Number of repetitions (default: 3)')

args = parser.parse_args()
args.cuda = True if not args.no_cuda and torch.cuda.is_available() else False
args.logging = True if not args.no_logging else False

assert args.alpha>=0. and args.alpha<=1.

print('Source domains: {}, {}'.format(args.source1, args.source2))
print('Target domain: {}, {}'.format(args.target1, args.target2))
print('Cuda Mode: {}'.format(args.cuda))
print('Batch size: {}'.format(args.batch_size))
print('LR task: {}'.format(args.lr_task))
print('LR domain: {}'.format(args.lr_domain))
print('L2: {}'.format(args.l2))
print('Alpha: {}'.format(args.alpha))
print('Momentum task: {}'.format(args.momentum_task))
print('Momentum domain: {}'.format(args.momentum_domain))
print('Nadir slack: {}'.format(args.nadir_slack))
print('RP size: {}'.format(args.rp_size))
print('Patience: {}'.format(args.patience))
print('Smoothing: {}'.format(args.smoothing))
print('Warmup its: {}'.format(args.warmup_its))
print('LR factor: {}'.format(args.factor))
print('Ablation: {}'.format(args.ablation))
print('Train mode: {}'.format(args.train_mode))

acc_runs = []
seeds = [1, 10, 100]

for run in range(args.n_runs):
	print('Run {}'.format(run))

	# Setting seed
	random.seed(seeds[run])
	torch.manual_seed(seeds[run])
	checkpoint_path = os.path.join(args.checkpoint_path, args.target1+'_'+args.target2+'_seed'+str(seeds[run]))

	if args.cuda:
		torch.cuda.manual_seed(seeds[run])

	img_transform_train = transforms.Compose([transforms.RandomResizedCrop(225, scale=(0.7,1.0)), transforms.RandomHorizontalFlip(), transforms.ToTensor(), transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
	img_transform_test = transforms.Compose([transforms.Resize((225, 225)), transforms.ToTensor(), transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])

	train_source_1 = args.data_path + args.source1 + '/train/'
	train_source_2 = args.data_path + args.source2 + '/train/'
	test_source_1 = args.data_path + args.source1 + '/test/'
	test_source_2 = args.data_path + args.source2 + '/test/'
	target_path_1 = args.data_path + args.target1 + '/test/'
	target_path_2 = args.data_path + args.target2 + '/test/'

	source_dataset = Loader_unif_sampling(path1=train_source_1, path2=train_source_2, transform=img_transform_train)
	source_loader = torch.utils.data.DataLoader(dataset=source_dataset, batch_size=args.batch_size, shuffle=True, num_workers=args.workers)

	test_source_dataset = Loader_unif_sampling(path1=test_source_1, path2=test_source_2, transform=img_transform_test)
	test_source_loader = torch.utils.data.DataLoader(dataset=test_source_dataset, batch_size=args.batch_size, shuffle=True, num_workers=args.workers)
	
	target_dataset = Loader_validation(path1=target_path_1, transform=img_transform_test)
	target_loader = torch.utils.data.DataLoader(dataset=target_dataset, batch_size=args.batch_size, shuffle=True, num_workers=args.workers)
		
	task_classifier = models.task_classifier()
	domain_discriminator_list = []
	for i in range(1):
		if args.rp_size == 4096 or args.ablation == 'RP':
			disc = models.domain_discriminator_ablation_RP(optim.SGD, args.lr_domain, args.momentum_domain, args.l2).train()
		else:
			disc = models.domain_discriminator(args.rp_size, optim.SGD, args.lr_domain, args.momentum_domain, args.l2).train()
		domain_discriminator_list.append(disc)	
		
	feature_extractor = models.AlexNet(num_classes = 5, baseline = False)
	state_dict = torch.load("../alexnet_caffe.pth.tar")
	del state_dict["classifier.fc8.weight"]
	del state_dict["classifier.fc8.bias"]
	not_loaded = feature_extractor.load_state_dict(state_dict, strict = False)

	optimizer_task = optim.SGD(list(feature_extractor.parameters())+list(task_classifier.parameters()), lr=args.lr_task, momentum=args.momentum_task, weight_decay = args.l2)

	models_dict = {}
	models_dict['feature_extractor'] = feature_extractor
	models_dict['task_classifier'] = task_classifier
	models_dict['domain_discriminator_list'] = domain_discriminator_list

	if args.cuda:
		for key in models_dict.keys():
			if key != 'domain_discriminator_list':
				models_dict[key] = models_dict[key].cuda()
			else:
				for k, disc in enumerate(models_dict[key]):
					models_dict[key][k] = disc.cuda()
		torch.backends.cudnn.benchmark = True
			
	trainer = TrainLoop(models_dict, optimizer_task, source_loader, test_source_loader, target_loader, args.nadir_slack, args.alpha, args.patience, args.factor, args.smoothing, args.warmup_its, args.lr_threshold, checkpoint_path=args.checkpoint_path, checkpoint_epoch=args.checkpoint_epoch, cuda=args.cuda, ablation=args.ablation, logging=args.logging, train_mode=args.train_mode)
	err = trainer.train(n_epochs=args.epochs, save_every=args.save_every)

	acc_runs.append(1-err)

print(acc_runs)

df = pandas.DataFrame(data={'Acc-{}'.format(args.target1,args.target2): acc_runs, 'Seed': seeds[:args.n_runs]})
df.to_csv('./accuracy_runs_'+args.target1+'_'+args.target2+'.csv', sep=',', index = False)
